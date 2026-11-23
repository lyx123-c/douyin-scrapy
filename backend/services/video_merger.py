import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.resource_lifecycle import (
    ProcessIdentity,
    ResourceCloseResult,
    ResourceState,
    ResourceTracker,
)
from config import FFMPEG_PATH


logger = logging.getLogger(__name__)

DEFAULT_FFMPEG_MERGE_TIMEOUT = 300.0
DEFAULT_FFMPEG_STDIN_Q_TIMEOUT = 1.5
DEFAULT_FFMPEG_TERMINATE_TIMEOUT = 2.0
DEFAULT_FFMPEG_KILL_TIMEOUT = 2.0
DEFAULT_FFMPEG_CLOSE_TIMEOUT = (
    DEFAULT_FFMPEG_STDIN_Q_TIMEOUT
    + DEFAULT_FFMPEG_TERMINATE_TIMEOUT
    + DEFAULT_FFMPEG_KILL_TIMEOUT
)
DEFAULT_FFMPEG_POLL_INTERVAL = 0.05
DEFAULT_STDERR_MAX_BYTES = 16 * 1024
DEFAULT_STDERR_JOIN_TIMEOUT = 1.0
MIN_VALID_MERGED_BYTES = 1024


class FFmpegProcessError(RuntimeError):
    """Base error raised when an FFmpeg merge does not complete successfully."""

    def __init__(
        self,
        message: str,
        *,
        result: "FFmpegRunResult | None" = None,
    ) -> None:
        super().__init__(message)
        self.result = result


class FFmpegCancelledError(FFmpegProcessError):
    """Raised when an FFmpeg merge is cancelled via stop signal."""


class FFmpegTimeoutError(FFmpegProcessError):
    """Raised when an FFmpeg merge exceeds its execution timeout or deadline."""


class FFmpegExitUnconfirmedError(FFmpegProcessError):
    """Raised when FFmpeg exit could not be verified after stop/termination."""


@dataclass
class FFmpegRunResult:
    """Detailed, verifiable result of a managed FFmpeg execution."""

    succeeded: bool
    cancelled: bool
    timed_out: bool
    exit_verified: bool
    returncode: int | None
    state: ResourceState
    stop_stage: str
    stderr_tail: str = ""
    stderr_truncated: bool = False
    stderr_total_bytes: int = 0
    stderr_reader_exited: bool = True
    pid: int | None = None
    output_file: str | None = None
    output_valid: bool = False
    error: str | None = None
    close_result: ResourceCloseResult | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


class _BoundedStderrReader:
    """Continuously drain FFmpeg stderr on a dedicated thread with bounded memory."""

    def __init__(
        self,
        stream,
        *,
        max_bytes: int = DEFAULT_STDERR_MAX_BYTES,
        name: str = "ffmpeg-stderr",
    ) -> None:
        self._stream = stream
        self._max_bytes = max(256, int(max_bytes))
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._total_bytes = 0
        self._truncated = False
        self._read_error: str | None = None
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        if stream is not None:
            self._thread = threading.Thread(
                target=self._drain,
                name=name,
                daemon=True,
            )
            self._thread.start()
        else:
            self._done.set()

    def _drain(self) -> None:
        try:
            read_fn = getattr(self._stream, "read", None)
            if read_fn is None:
                return
            while True:
                chunk = read_fn(4096)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    raw = chunk.encode("utf-8", errors="replace")
                else:
                    raw = bytes(chunk)
                with self._lock:
                    self._total_bytes += len(raw)
                    self._buffer.extend(raw)
                    if len(self._buffer) > self._max_bytes:
                        overflow = len(self._buffer) - self._max_bytes
                        del self._buffer[:overflow]
                        self._truncated = True
        except Exception as exc:
            with self._lock:
                self._read_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._done.set()

    def join(self, timeout: float = DEFAULT_STDERR_JOIN_TIMEOUT) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout=max(0.0, float(timeout)))
        return not self._thread.is_alive()

    @property
    def exited(self) -> bool:
        return self._thread is None or not self._thread.is_alive()

    @property
    def truncated(self) -> bool:
        with self._lock:
            return self._truncated

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def read_error(self) -> str | None:
        with self._lock:
            return self._read_error

    def tail_text(self) -> str:
        with self._lock:
            raw = bytes(self._buffer)
        return raw.decode("utf-8", errors="replace")


def build_ffmpeg_merge_command(
    video_file: str,
    audio_file: str,
    output_file: str,
    *,
    ffmpeg_path: str | None = None,
) -> list[str]:
    """Build the exact FFmpeg merge command without altering media quality settings."""
    return [
        ffmpeg_path or FFMPEG_PATH,
        "-hide_banner",
        "-y",
        "-i",
        video_file,
        "-i",
        audio_file,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        output_file,
    ]


def validate_merged_output(
    output_file: str,
    *,
    min_bytes: int = MIN_VALID_MERGED_BYTES,
) -> tuple[bool, str]:
    """Validate that a merged output file exists, is a regular file, and is non-trivial."""
    if not output_file:
        return False, "输出文件路径为空。"
    if not os.path.exists(output_file):
        return False, "输出文件不存在。"
    if not os.path.isfile(output_file):
        return False, "输出路径不是普通文件。"
    try:
        size = os.path.getsize(output_file)
    except OSError as exc:
        return False, f"无法读取输出文件大小：{exc}"
    if size < min_bytes:
        return False, f"输出文件过小，仅 {size} 字节。"
    return True, f"valid:{size}"


class ManagedFFmpegProcess:
    """
    Explicitly owned, verifiable FFmpeg subprocess lifecycle wrapper.

    Implements the lightweight ManagedResource contract:
    - Popen-based execution with explicit handle & ProcessIdentity
    - Dedicated bounded stderr reader to prevent pipe deadlocks
    - stdout redirected to DEVNULL to prevent output pipe deadlocks
    - Stop-signal observation and bounded shared-deadline shutdown
    - Escalation: stdin 'q' -> terminate -> kill -> OS exit verification
    - Idempotent close() that never reports success unless exit is verified
    """

    resource_type = "ffmpeg"

    def __init__(
        self,
        command: list[str],
        *,
        owner: str = "video_merger",
        resource_id: str | None = None,
        resource_tracker: ResourceTracker | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        stdin_q_timeout: float = DEFAULT_FFMPEG_STDIN_Q_TIMEOUT,
        terminate_timeout: float = DEFAULT_FFMPEG_TERMINATE_TIMEOUT,
        kill_timeout: float = DEFAULT_FFMPEG_KILL_TIMEOUT,
        poll_interval: float = DEFAULT_FFMPEG_POLL_INTERVAL,
        stderr_max_bytes: int = DEFAULT_STDERR_MAX_BYTES,
        stderr_join_timeout: float = DEFAULT_STDERR_JOIN_TIMEOUT,
    ) -> None:
        self.resource_id = resource_id or f"ffmpeg-{uuid.uuid4().hex[:12]}"
        self.owner = str(owner).strip() or "video_merger"
        self._command = list(command)
        self._tracker = resource_tracker
        self._popen_factory = popen_factory
        self._stdin_q_timeout = max(0.0, float(stdin_q_timeout))
        self._terminate_timeout = max(0.0, float(terminate_timeout))
        self._kill_timeout = max(0.0, float(kill_timeout))
        self._poll_interval = max(0.005, float(poll_interval))
        self._stderr_max_bytes = max(256, int(stderr_max_bytes))
        self._stderr_join_timeout = max(0.05, float(stderr_join_timeout))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._state = ResourceState.CREATED
        self._proc: Any | None = None
        self._identity: ProcessIdentity | None = None
        self._stderr_reader: _BoundedStderrReader | None = None
        self._registered = False
        self._stop_reason: str = ""
        self._stop_deadline: float | None = None
        self._stop_stage: str = "none"
        self._spawn_error: str | None = None
        self._last_close_result: ResourceCloseResult | None = None
        self._closing = False
        self._close_done = threading.Event()
        self._close_done.set()

        self._diagnostics: dict[str, Any] = {
            "stop_requested": False,
            "stdin_q_sent": False,
            "terminate_executed": False,
            "terminate_guard_reason": "",
            "kill_executed": False,
            "kill_guard_reason": "",
            "exit_verification_basis": "",
        }

    @property
    def state(self) -> ResourceState:
        with self._lock:
            return self._state

    @property
    def pid(self) -> int | None:
        with self._lock:
            if self._identity is not None:
                return self._identity.pid
            return getattr(self._proc, "pid", None) if self._proc is not None else None

    @property
    def identity(self) -> ProcessIdentity | None:
        with self._lock:
            return self._identity

    @property
    def returncode(self) -> int | None:
        with self._lock:
            proc = self._proc
        if proc is None:
            return None
        try:
            rc = proc.poll()
        except Exception:
            rc = getattr(proc, "returncode", None)
        return rc

    def start(self) -> "ManagedFFmpegProcess":
        with self._lock:
            if self._state != ResourceState.CREATED:
                raise RuntimeError(
                    f"ManagedFFmpegProcess 不可重复启动，当前状态：{self._state.value}"
                )

        if self._popen_factory is subprocess.Popen:
            executable = str(self._command[0]) if self._command else ""
            if not executable or not os.path.isfile(executable):
                error_msg = (
                    f"FFmpeg 可执行文件不存在：{executable or '<empty>'}。"
                    "请检查 resources/ffmpeg/ffmpeg.exe 或 FFMPEG_PATH。"
                )
                with self._lock:
                    self._state = ResourceState.STOP_FAILED
                    self._spawn_error = error_msg
                    self._diagnostics["spawn_error"] = error_msg
                raise FFmpegProcessError(
                    error_msg,
                    result=FFmpegRunResult(
                        succeeded=False,
                        cancelled=False,
                        timed_out=False,
                        exit_verified=True,
                        returncode=None,
                        state=ResourceState.STOP_FAILED,
                        stop_stage="spawn_failed",
                        pid=None,
                        error=error_msg,
                        diagnostics=dict(self._diagnostics),
                    ),
                )

        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
        }
        if (
            os.name == "nt"
            and self._popen_factory is subprocess.Popen
            and hasattr(subprocess, "CREATE_NO_WINDOW")
        ):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            proc = self._popen_factory(self._command, **popen_kwargs)
        except Exception as exc:
            error_msg = f"FFmpeg 启动失败：{type(exc).__name__}: {exc}"
            with self._lock:
                self._state = ResourceState.STOP_FAILED
                self._spawn_error = error_msg
                self._diagnostics["spawn_error"] = error_msg
            raise FFmpegProcessError(
                error_msg,
                result=FFmpegRunResult(
                    succeeded=False,
                    cancelled=False,
                    timed_out=False,
                    exit_verified=True,
                    returncode=None,
                    state=ResourceState.STOP_FAILED,
                    stop_stage="spawn_failed",
                    pid=None,
                    error=error_msg,
                    diagnostics=dict(self._diagnostics),
                ),
            ) from exc

        identity = ProcessIdentity.capture(proc)
        stderr_reader = _BoundedStderrReader(
            getattr(proc, "stderr", None),
            max_bytes=self._stderr_max_bytes,
            name=f"{self.resource_id}-stderr",
        )

        with self._lock:
            self._proc = proc
            self._identity = identity
            self._stderr_reader = stderr_reader
            self._state = ResourceState.RUNNING

        if self._tracker is not None:
            try:
                self._tracker.register(self)
                with self._lock:
                    self._registered = True
            except Exception:
                # If registration fails, immediately close the spawned process so
                # no unmanaged FFmpeg subprocess can leak.
                self.close(reason="registration_failed")
                raise

        return self

    def request_stop(
        self,
        reason: str = "stop_requested",
        *,
        deadline: float | None = None,
    ) -> bool:
        """Idempotently request FFmpeg cancellation (non-blocking)."""
        with self._lock:
            if deadline is not None:
                if self._stop_deadline is None or deadline < self._stop_deadline:
                    self._stop_deadline = deadline
            if self._state in (ResourceState.STOPPED, ResourceState.STOP_FAILED):
                return False
            already_set = self._stop_event.is_set()
            self._stop_event.set()
            if not self._stop_reason:
                self._stop_reason = reason or "stop_requested"
            self._diagnostics["stop_requested"] = True
            if self._state == ResourceState.RUNNING:
                self._state = ResourceState.STOP_REQUESTED
            return not already_set

    def _safe_close_stream(self, stream) -> None:
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            pass

    def _finalize_confirmed_exit(
        self,
        *,
        basis: str,
        stop_stage: str,
    ) -> tuple[int | None, bool]:
        with self._lock:
            proc = self._proc
            identity = self._identity
            reader = self._stderr_reader

        rc = None
        if proc is not None:
            try:
                rc = proc.poll()
            except Exception:
                rc = getattr(proc, "returncode", None)

        if proc is not None:
            self._safe_close_stream(getattr(proc, "stdin", None))
            self._safe_close_stream(getattr(proc, "stdout", None))

        reader_exited = True
        if reader is not None:
            reader_exited = reader.join(timeout=self._stderr_join_timeout)
            if not reader_exited and proc is not None:
                self._safe_close_stream(getattr(proc, "stderr", None))
                reader_exited = reader.join(timeout=0.2)
        if proc is not None:
            self._safe_close_stream(getattr(proc, "stderr", None))

        if identity is not None:
            identity.close_handle()

        with self._lock:
            self._state = ResourceState.STOPPED
            self._stop_stage = stop_stage
            self._diagnostics["exit_verification_basis"] = basis
            self._diagnostics["stderr_reader_exited"] = reader_exited
            was_registered = self._registered
            self._registered = False

        if was_registered and self._tracker is not None:
            self._tracker.unregister(self.resource_id)

        return rc, reader_exited

    def _wait_until_verified_exit(
        self,
        proc: Any,
        identity: ProcessIdentity,
        stage_deadline: float,
    ) -> tuple[bool, str]:
        while True:
            exited, basis = identity.verify_exited(proc)
            if exited:
                return True, basis
            remaining = stage_deadline - time.monotonic()
            if remaining <= 0:
                return False, basis
            step = min(self._poll_interval, remaining)
            wait_fn = getattr(proc, "wait", None)
            if callable(wait_fn):
                try:
                    wait_fn(timeout=step)
                except subprocess.TimeoutExpired:
                    pass
                except Exception:
                    time.sleep(step)
            else:
                time.sleep(step)

    def _attempt_stdin_q(self, proc: Any) -> bool:
        stdin = getattr(proc, "stdin", None)
        if stdin is None:
            return False
        try:
            if getattr(stdin, "closed", False):
                return False
            stdin.write(b"q\n")
            flush = getattr(stdin, "flush", None)
            if callable(flush):
                flush()
            stdin.close()
            return True
        except Exception:
            self._safe_close_stream(stdin)
            return False

    def verify_released(self) -> bool:
        with self._lock:
            state = self._state
            proc = self._proc
            identity = self._identity

        if proc is None:
            return state == ResourceState.STOPPED or self._spawn_error is not None

        if identity is None:
            return False

        exited, basis = identity.verify_exited(proc)
        if exited and state != ResourceState.STOPPED:
            self._finalize_confirmed_exit(
                basis=basis,
                stop_stage=self._stop_stage if self._stop_stage != "none" else "normal",
            )
        return exited

    def close(
        self,
        timeout: float = DEFAULT_FFMPEG_CLOSE_TIMEOUT,
        *,
        deadline: float | None = None,
        reason: str = "close",
    ) -> ResourceCloseResult:
        """
        Idempotently stop, terminate if needed, and verify exit of the owned FFmpeg process.

        Never reports closed=True unless process exit is verified.
        """
        close_deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + max(0.0, float(timeout))
        )

        with self._lock:
            if self._state == ResourceState.STOPPED and self._last_close_result is not None:
                return self._last_close_result
            if self._proc is None:
                if self._spawn_error is not None:
                    result = ResourceCloseResult(
                        resource_id=self.resource_id,
                        resource_type=self.resource_type,
                        owner=self.owner,
                        state=ResourceState.STOP_FAILED,
                        closed=True,
                        exit_verified=True,
                        reason=reason,
                        error=self._spawn_error,
                        diagnostics=dict(self._diagnostics),
                    )
                    self._last_close_result = result
                    return result
                self._state = ResourceState.STOPPED
                result = ResourceCloseResult(
                    resource_id=self.resource_id,
                    resource_type=self.resource_type,
                    owner=self.owner,
                    state=ResourceState.STOPPED,
                    closed=True,
                    exit_verified=True,
                    graceful=True,
                    reason=reason,
                    diagnostics=dict(self._diagnostics),
                )
                self._last_close_result = result
                return result

            if self._closing:
                wait_event = self._close_done
                is_owner = False
            else:
                self._closing = True
                self._close_done = threading.Event()
                self._stop_event.set()
                if not self._stop_reason:
                    self._stop_reason = reason
                self._diagnostics["stop_requested"] = True
                is_owner = True

        if not is_owner:
            remaining = max(0.0, close_deadline - time.monotonic())
            wait_event.wait(timeout=remaining)
            with self._lock:
                if self._last_close_result is not None:
                    return self._last_close_result
                return ResourceCloseResult(
                    resource_id=self.resource_id,
                    resource_type=self.resource_type,
                    owner=self.owner,
                    state=self._state,
                    closed=self._state == ResourceState.STOPPED,
                    exit_verified=self._state == ResourceState.STOPPED,
                    timed_out=True,
                    reason=reason,
                    error="等待其他线程完成 FFmpeg 关闭超时。",
                    diagnostics=dict(self._diagnostics),
                )

        try:
            with self._lock:
                proc = self._proc
                identity = self._identity

            assert proc is not None and identity is not None

            # Already exited before we even need to signal?
            exited, basis = identity.verify_exited(proc)
            if exited:
                rc, _ = self._finalize_confirmed_exit(basis=basis, stop_stage="normal")
                result = ResourceCloseResult(
                    resource_id=self.resource_id,
                    resource_type=self.resource_type,
                    owner=self.owner,
                    state=ResourceState.STOPPED,
                    closed=True,
                    exit_verified=True,
                    graceful=True,
                    forced=False,
                    timed_out=reason == "timeout",
                    returncode=rc,
                    reason=reason,
                    diagnostics=dict(self._diagnostics),
                )
                with self._lock:
                    self._last_close_result = result
                return result

            total_budget = max(0.0, close_deadline - time.monotonic())
            stage_sum = (
                self._stdin_q_timeout
                + self._terminate_timeout
                + self._kill_timeout
            )
            scale = min(1.0, total_budget / stage_sum) if stage_sum > 0 else 0.0
            q_budget = self._stdin_q_timeout * scale
            term_budget = self._terminate_timeout * scale

            # Stage 1: Graceful stdin 'q'
            with self._lock:
                self._state = ResourceState.STOPPING

            if q_budget > 0.0:
                q_sent = self._attempt_stdin_q(proc)
                with self._lock:
                    self._diagnostics["stdin_q_sent"] = q_sent
                if q_sent:
                    q_deadline = min(close_deadline, time.monotonic() + q_budget)
                    exited, basis = self._wait_until_verified_exit(
                        proc,
                        identity,
                        q_deadline,
                    )
                    if exited:
                        rc, _ = self._finalize_confirmed_exit(
                            basis=basis,
                            stop_stage="stdin_q",
                        )
                        result = ResourceCloseResult(
                            resource_id=self.resource_id,
                            resource_type=self.resource_type,
                            owner=self.owner,
                            state=ResourceState.STOPPED,
                            closed=True,
                            exit_verified=True,
                            graceful=True,
                            forced=False,
                            timed_out=reason == "timeout",
                            returncode=rc,
                            reason=reason,
                            diagnostics=dict(self._diagnostics),
                        )
                        with self._lock:
                            self._last_close_result = result
                        return result

            # Stage 2: Owned terminate()
            with self._lock:
                self._state = ResourceState.FORCE_STOPPING

            can_term, term_guard = identity.can_safely_terminate(proc)
            with self._lock:
                self._diagnostics["terminate_guard_reason"] = term_guard

            if can_term:
                try:
                    proc.terminate()
                    with self._lock:
                        self._diagnostics["terminate_executed"] = True
                except Exception as exc:
                    with self._lock:
                        self._diagnostics["terminate_error"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                term_deadline = min(close_deadline, time.monotonic() + term_budget)
                exited, basis = self._wait_until_verified_exit(
                    proc,
                    identity,
                    term_deadline,
                )
                if exited:
                    rc, _ = self._finalize_confirmed_exit(
                        basis=basis,
                        stop_stage="terminate",
                    )
                    result = ResourceCloseResult(
                        resource_id=self.resource_id,
                        resource_type=self.resource_type,
                        owner=self.owner,
                        state=ResourceState.STOPPED,
                        closed=True,
                        exit_verified=True,
                        graceful=False,
                        forced=True,
                        timed_out=reason == "timeout",
                        returncode=rc,
                        reason=reason,
                        diagnostics=dict(self._diagnostics),
                    )
                    with self._lock:
                        self._last_close_result = result
                    return result
            else:
                exited, basis = identity.verify_exited(proc)
                if exited:
                    rc, _ = self._finalize_confirmed_exit(
                        basis=basis,
                        stop_stage="normal",
                    )
                    result = ResourceCloseResult(
                        resource_id=self.resource_id,
                        resource_type=self.resource_type,
                        owner=self.owner,
                        state=ResourceState.STOPPED,
                        closed=True,
                        exit_verified=True,
                        graceful=True,
                        forced=False,
                        timed_out=reason == "timeout",
                        returncode=rc,
                        reason=reason,
                        diagnostics=dict(self._diagnostics),
                    )
                    with self._lock:
                        self._last_close_result = result
                    return result

            # Stage 3: Owned kill()
            can_kill, kill_guard = identity.can_safely_terminate(proc)
            with self._lock:
                self._diagnostics["kill_guard_reason"] = kill_guard

            if can_kill:
                try:
                    proc.kill()
                    with self._lock:
                        self._diagnostics["kill_executed"] = True
                except Exception as exc:
                    with self._lock:
                        self._diagnostics["kill_error"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                exited, basis = self._wait_until_verified_exit(
                    proc,
                    identity,
                    close_deadline,
                )
                if exited:
                    rc, _ = self._finalize_confirmed_exit(
                        basis=basis,
                        stop_stage="kill",
                    )
                    result = ResourceCloseResult(
                        resource_id=self.resource_id,
                        resource_type=self.resource_type,
                        owner=self.owner,
                        state=ResourceState.STOPPED,
                        closed=True,
                        exit_verified=True,
                        graceful=False,
                        forced=True,
                        timed_out=reason == "timeout",
                        returncode=rc,
                        reason=reason,
                        diagnostics=dict(self._diagnostics),
                    )
                    with self._lock:
                        self._last_close_result = result
                    return result

            # Final verification failed: DO NOT fake closed=True or unregister!
            exited, basis = identity.verify_exited(proc)
            if exited:
                rc, _ = self._finalize_confirmed_exit(
                    basis=basis,
                    stop_stage="kill" if self._diagnostics["kill_executed"] else "terminate",
                )
                result = ResourceCloseResult(
                    resource_id=self.resource_id,
                    resource_type=self.resource_type,
                    owner=self.owner,
                    state=ResourceState.STOPPED,
                    closed=True,
                    exit_verified=True,
                    graceful=False,
                    forced=True,
                    timed_out=reason == "timeout",
                    returncode=rc,
                    reason=reason,
                    diagnostics=dict(self._diagnostics),
                )
                with self._lock:
                    self._last_close_result = result
                return result

            error_msg = f"FFmpeg 进程退出未能确认（pid={identity.pid}, basis={basis}）。"
            with self._lock:
                self._state = ResourceState.STOP_FAILED
                self._stop_stage = "unconfirmed"
                self._diagnostics["exit_verification_basis"] = basis
                self._diagnostics["error"] = error_msg
                result = ResourceCloseResult(
                    resource_id=self.resource_id,
                    resource_type=self.resource_type,
                    owner=self.owner,
                    state=ResourceState.STOP_FAILED,
                    closed=False,
                    exit_verified=False,
                    graceful=False,
                    forced=bool(
                        self._diagnostics["terminate_executed"]
                        or self._diagnostics["kill_executed"]
                    ),
                    timed_out=True,
                    returncode=None,
                    reason=reason,
                    error=error_msg,
                    diagnostics=dict(self._diagnostics),
                )
                self._last_close_result = result
            logger.warning("%s", error_msg)
            return result
        finally:
            with self._lock:
                self._closing = False
                self._close_done.set()

    def wait(
        self,
        *,
        stop_event: threading.Event | None = None,
        timeout: float = DEFAULT_FFMPEG_MERGE_TIMEOUT,
        deadline: float | None = None,
        close_timeout: float | None = None,
        output_file: str | None = None,
    ) -> FFmpegRunResult:
        """
        Observe execution until normal completion, stop_event cancellation, or deadline.

        Always uses bounded waits and returns a verified FFmpegRunResult.
        """
        with self._lock:
            proc = self._proc
            identity = self._identity

        if proc is None or identity is None:
            raise RuntimeError("ManagedFFmpegProcess 尚未启动。")

        now = time.monotonic()
        run_deadline = now + max(0.0, float(timeout))
        if deadline is not None:
            run_deadline = min(run_deadline, deadline)

        effective_close_timeout = (
            DEFAULT_FFMPEG_CLOSE_TIMEOUT
            if close_timeout is None
            else max(0.0, float(close_timeout))
        )
        # When an external shared deadline is supplied (e.g., Runtime shutdown),
        # reserve the close window inside that shared deadline so total time never stacks.
        if deadline is not None and effective_close_timeout > 0:
            execution_cutoff = max(now, deadline - effective_close_timeout)
        else:
            execution_cutoff = run_deadline

        cancelled = False
        timed_out = False
        close_result: ResourceCloseResult | None = None

        while True:
            stop_now = self._stop_event.is_set() or bool(
                stop_event is not None and stop_event.is_set()
            )
            exited, basis = identity.verify_exited(proc)

            if stop_now:
                cancelled = True
                self.request_stop("cancelled")
                with self._lock:
                    stop_deadline = self._stop_deadline
                candidates = [
                    d
                    for d in (deadline, stop_deadline)
                    if d is not None
                ]
                close_deadline = (
                    min(candidates)
                    if candidates
                    else time.monotonic() + effective_close_timeout
                )
                close_result = self.close(
                    timeout=effective_close_timeout,
                    deadline=close_deadline,
                    reason="cancelled",
                )
                break

            if exited:
                rc, reader_exited = self._finalize_confirmed_exit(
                    basis=basis,
                    stop_stage="normal",
                )
                # Guard cancellation race: if stop arrived right as process finished,
                # treat as cancelled so caller never commits a cancelled task.
                if self._stop_event.is_set() or bool(
                    stop_event is not None and stop_event.is_set()
                ):
                    cancelled = True
                close_result = ResourceCloseResult(
                    resource_id=self.resource_id,
                    resource_type=self.resource_type,
                    owner=self.owner,
                    state=ResourceState.STOPPED,
                    closed=True,
                    exit_verified=True,
                    graceful=True,
                    forced=False,
                    timed_out=False,
                    returncode=rc,
                    reason="cancelled" if cancelled else "normal",
                    diagnostics=dict(self._diagnostics),
                )
                with self._lock:
                    self._last_close_result = close_result
                break

            remaining = min(run_deadline, execution_cutoff) - time.monotonic()
            if remaining <= 0:
                timed_out = True
                close_deadline = (
                    deadline
                    if deadline is not None
                    else time.monotonic() + effective_close_timeout
                )
                close_result = self.close(
                    timeout=effective_close_timeout,
                    deadline=close_deadline,
                    reason="timeout",
                )
                break

            step = min(self._poll_interval, remaining)
            if stop_event is not None:
                stop_event.wait(timeout=step)
            else:
                self._stop_event.wait(timeout=step)

        assert close_result is not None
        reader = self._stderr_reader
        stderr_tail = reader.tail_text() if reader is not None else ""
        stderr_truncated = reader.truncated if reader is not None else False
        stderr_total = reader.total_bytes if reader is not None else 0
        reader_exited = reader.exited if reader is not None else True

        output_valid = False
        output_error: str | None = None
        if (
            close_result.exit_verified
            and not cancelled
            and not timed_out
            and close_result.returncode == 0
        ):
            if output_file is not None:
                output_valid, detail = validate_merged_output(output_file)
                if not output_valid:
                    output_error = detail
            else:
                output_valid = True

        succeeded = bool(
            close_result.exit_verified
            and not cancelled
            and not timed_out
            and close_result.returncode == 0
            and output_valid
        )

        error = close_result.error or output_error
        if not succeeded and error is None:
            if cancelled:
                error = "FFmpeg 合并已取消。"
            elif timed_out:
                error = "FFmpeg 合并超时。"
            elif close_result.returncode not in (None, 0):
                error = (
                    stderr_tail[-1200:].strip()
                    or f"FFmpeg 合并失败，退出码：{close_result.returncode}"
                )

        return FFmpegRunResult(
            succeeded=succeeded,
            cancelled=cancelled,
            timed_out=timed_out,
            exit_verified=close_result.exit_verified,
            returncode=close_result.returncode,
            state=close_result.state,
            stop_stage=self._stop_stage,
            stderr_tail=stderr_tail,
            stderr_truncated=stderr_truncated,
            stderr_total_bytes=stderr_total,
            stderr_reader_exited=reader_exited,
            pid=identity.pid,
            output_file=output_file,
            output_valid=output_valid,
            error=error,
            close_result=close_result,
            diagnostics=dict(self._diagnostics),
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "resource_id": self.resource_id,
                "resource_type": self.resource_type,
                "owner": self.owner,
                "state": self._state.value,
                "pid": self._identity.pid if self._identity else None,
                "identity": self._identity.snapshot() if self._identity else None,
                "stop_stage": self._stop_stage,
                "stop_reason": self._stop_reason,
                "stderr_reader_exited": (
                    self._stderr_reader.exited if self._stderr_reader else True
                ),
                "diagnostics": dict(self._diagnostics),
            }


def merge_video_and_audio(
    video_file: str,
    audio_file: str,
    output_file: str,
    *,
    stop_event: threading.Event | None = None,
    timeout: float = DEFAULT_FFMPEG_MERGE_TIMEOUT,
    deadline: float | None = None,
    close_timeout: float | None = None,
    resource_tracker: ResourceTracker | None = None,
    owner: str = "video_merger",
    popen_factory: Callable[..., Any] = subprocess.Popen,
    ffmpeg_path: str | None = None,
) -> FFmpegRunResult:
    """
    Merge video and audio streams using ManagedFFmpegProcess with verifiable exit.
    """
    if stop_event is not None and stop_event.is_set():
        result = FFmpegRunResult(
            succeeded=False,
            cancelled=True,
            timed_out=False,
            exit_verified=True,
            returncode=None,
            state=ResourceState.STOPPED,
            stop_stage="cancelled_before_start",
            output_file=output_file,
            output_valid=False,
            error="FFmpeg 合并在启动前已取消。",
        )
        raise FFmpegCancelledError("FFmpeg 合并在启动前已取消。", result=result)

    command = build_ffmpeg_merge_command(
        video_file,
        audio_file,
        output_file,
        ffmpeg_path=ffmpeg_path,
    )
    process = ManagedFFmpegProcess(
        command,
        owner=owner,
        resource_tracker=resource_tracker,
        popen_factory=popen_factory,
    )
    process.start()
    result = process.wait(
        stop_event=stop_event,
        timeout=timeout,
        deadline=deadline,
        close_timeout=close_timeout,
        output_file=output_file,
    )

    if not result.exit_verified:
        raise FFmpegExitUnconfirmedError(
            result.error or "FFmpeg 进程退出未能确认。",
            result=result,
        )
    if result.cancelled:
        raise FFmpegCancelledError(
            result.error or "FFmpeg 合并已取消。",
            result=result,
        )
    if result.timed_out:
        raise FFmpegTimeoutError(
            result.error or "FFmpeg 合并超时。",
            result=result,
        )
    if result.returncode != 0:
        raise FFmpegProcessError(
            result.stderr_tail[-1200:] or result.error or f"FFmpeg 退出码异常：{result.returncode}",
            result=result,
        )
    if not result.output_valid:
        raise FFmpegProcessError(
            result.error or "FFmpeg 输出文件无效。",
            result=result,
        )
    return result
