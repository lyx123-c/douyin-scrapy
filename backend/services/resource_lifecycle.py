import ctypes
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is installed in runtime env
    psutil = None


logger = logging.getLogger(__name__)

# Windows kernel32 constants for safe handle-based identity & exit verification
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259
_CREATE_TIME_TOLERANCE_SECONDS = 0.05


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


def _filetime_to_int(ft: _FILETIME) -> int:
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def _get_kernel32():
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.DuplicateHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.c_bool,
        ctypes.c_uint32,
    ]
    kernel32.DuplicateHandle.restype = ctypes.c_bool
    kernel32.OpenProcess.argtypes = [
        ctypes.c_uint32,
        ctypes.c_bool,
        ctypes.c_uint32,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_bool
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    return kernel32


class ResourceState(str, Enum):
    """Minimal lifecycle states for explicitly managed runtime resources."""

    CREATED = "created"
    RUNNING = "running"
    STOP_REQUESTED = "stop_requested"
    STOPPING = "stopping"
    FORCE_STOPPING = "force_stopping"
    STOPPED = "stopped"
    STOP_FAILED = "stop_failed"


@dataclass
class ResourceCloseResult:
    """Verifiable close/release outcome for a managed resource."""

    resource_id: str
    resource_type: str
    owner: str
    state: ResourceState
    closed: bool
    exit_verified: bool
    graceful: bool = False
    forced: bool = False
    timed_out: bool = False
    returncode: int | None = None
    reason: str = ""
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "owner": self.owner,
            "state": self.state.value,
            "closed": self.closed,
            "exit_verified": self.exit_verified,
            "graceful": self.graceful,
            "forced": self.forced,
            "timed_out": self.timed_out,
            "returncode": self.returncode,
            "reason": self.reason,
            "error": self.error,
            "diagnostics": dict(self.diagnostics),
        }


class ProcessIdentity:
    """
    Process identity binding for an owned child process.

    Combines PID, OS creation timestamp, and (on Windows) a duplicated
    non-terminating query/wait native handle so exit verification and
    termination guards are protected against PID reuse.
    """

    def __init__(
        self,
        *,
        pid: int | None,
        create_time: float | None = None,
        creation_filetime: int | None = None,
        native_handle: int | None = None,
        owns_native_handle: bool = False,
        popen_has_native_handle: bool = False,
        verification_basis: str = "unverified",
        captured_at: float | None = None,
    ) -> None:
        self.pid = pid
        self.create_time = create_time
        self.creation_filetime = creation_filetime
        self._native_handle = native_handle
        self._owns_native_handle = owns_native_handle
        self._popen_has_native_handle = popen_has_native_handle
        self.verification_basis = verification_basis
        self.captured_at = captured_at if captured_at is not None else time.monotonic()
        self.exit_code: int | None = None
        self.exited: bool = False
        self._handle_lock = threading.Lock()

    @property
    def native_handle(self) -> int | None:
        with self._handle_lock:
            return self._native_handle

    @property
    def identity_verified(self) -> bool:
        return bool(
            self.pid is not None
            and (
                self.native_handle is not None
                or self.creation_filetime is not None
                or self.create_time is not None
            )
        )

    @classmethod
    def capture(cls, proc: Any) -> "ProcessIdentity":
        pid = getattr(proc, "pid", None)
        if pid is not None:
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                pid = None

        raw_popen_handle = getattr(proc, "_handle", None)
        popen_handle_int: int | None = None
        if raw_popen_handle is not None and not isinstance(raw_popen_handle, bool):
            try:
                popen_handle_int = int(raw_popen_handle)
            except (TypeError, ValueError):
                popen_handle_int = None

        native_handle: int | None = None
        owns_native_handle = False
        creation_filetime: int | None = None
        create_time: float | None = None

        kernel32 = _get_kernel32()
        if kernel32 is not None and popen_handle_int:
            dup_handle = ctypes.c_void_p()
            current_proc = kernel32.GetCurrentProcess()
            duplicated = kernel32.DuplicateHandle(
                current_proc,
                ctypes.c_void_p(popen_handle_int),
                current_proc,
                ctypes.byref(dup_handle),
                _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                0,
            )
            if duplicated and dup_handle.value:
                native_handle = int(dup_handle.value)
                owns_native_handle = True
                creation_ft = _FILETIME()
                exit_ft = _FILETIME()
                kernel_ft = _FILETIME()
                user_ft = _FILETIME()
                if kernel32.GetProcessTimes(
                    ctypes.c_void_p(native_handle),
                    ctypes.byref(creation_ft),
                    ctypes.byref(exit_ft),
                    ctypes.byref(kernel_ft),
                    ctypes.byref(user_ft),
                ):
                    creation_filetime = _filetime_to_int(creation_ft)

        if psutil is not None and pid is not None and pid > 0:
            try:
                ps_proc = psutil.Process(pid)
                # Only trust psutil create_time if this is a real OS process or
                # backed by a real Popen handle (avoid accidental match on fake mock PIDs).
                if popen_handle_int is not None or isinstance(proc, subprocess.Popen):
                    create_time = float(ps_proc.create_time())
            except Exception:
                pass

        basis_parts = []
        if native_handle is not None:
            basis_parts.append("native_handle")
        if creation_filetime is not None:
            basis_parts.append("win32_creation_time")
        if create_time is not None:
            basis_parts.append("psutil_create_time")
        if not basis_parts:
            basis = "popen_only" if pid is not None else "unverified"
        else:
            basis = "+".join(basis_parts)

        return cls(
            pid=pid,
            create_time=create_time,
            creation_filetime=creation_filetime,
            native_handle=native_handle,
            owns_native_handle=owns_native_handle,
            popen_has_native_handle=popen_handle_int is not None,
            verification_basis=basis,
        )

    def _query_handle_exit(self) -> tuple[bool | None, int | None]:
        """Return (is_exited_or_None, exit_code_or_None) from native handle."""
        with self._handle_lock:
            handle = self._native_handle
        if handle is None:
            return None, None
        kernel32 = _get_kernel32()
        if kernel32 is None:
            return None, None

        wait_rc = kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0)
        if wait_rc == _WAIT_OBJECT_0:
            code = ctypes.c_uint32(0)
            if kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                # Convert unsigned Win32 DWORD to signed if needed only on caller side;
                # keep Python Popen returncode authoritative when available.
                return True, int(code.value)
            return True, None
        if wait_rc == _WAIT_TIMEOUT:
            return False, None
        return None, None

    def matches_live_process(self) -> bool:
        """
        Verify whether the OS process currently at `self.pid` is still this
        exact process instance (guards against Windows PID reuse).
        """
        if self.pid is None or self.pid <= 0:
            return False

        handle_exited, _ = self._query_handle_exit()
        if handle_exited is True:
            return False
        if handle_exited is False:
            return True

        if self.create_time is not None and psutil is not None:
            try:
                live_proc = psutil.Process(self.pid)
                live_create_time = float(live_proc.create_time())
                return (
                    abs(live_create_time - self.create_time)
                    <= _CREATE_TIME_TOLERANCE_SECONDS
                )
            except Exception:
                return False

        return False

    def verify_exited(self, proc: Any | None = None) -> tuple[bool, str]:
        """
        Verify whether the owned process has genuinely exited.

        Never treats stop/terminate/kill invocation as exit proof.
        """
        poll_rc: int | None = None
        if proc is not None:
            try:
                poll_rc = proc.poll()
            except Exception as exc:
                return False, f"poll_error:{type(exc).__name__}"

        handle_exited, handle_code = self._query_handle_exit()
        if handle_exited is True:
            self.exited = True
            self.exit_code = poll_rc if poll_rc is not None else handle_code
            return True, "native_handle_signaled"
        if handle_exited is False:
            return False, "native_handle_still_active"

        if poll_rc is not None:
            if self.create_time is not None and psutil is not None and self.pid:
                if self.matches_live_process():
                    return False, "os_process_still_matches_identity"
                self.exited = True
                self.exit_code = poll_rc
                return True, "poll_and_create_time_verified"
            self.exited = True
            self.exit_code = poll_rc
            return True, "popen_poll_verified"

        if self.create_time is not None and psutil is not None and self.pid:
            try:
                live_proc = psutil.Process(self.pid)
                live_create_time = float(live_proc.create_time())
                if (
                    abs(live_create_time - self.create_time)
                    > _CREATE_TIME_TOLERANCE_SECONDS
                ):
                    self.exited = True
                    return True, "pid_reused_original_exited"
                return False, "psutil_still_running"
            except Exception as exc:
                if psutil is not None and isinstance(exc, psutil.NoSuchProcess):
                    self.exited = True
                    return True, "psutil_no_such_process"
                return False, f"psutil_inconclusive:{type(exc).__name__}"

        return False, "process_still_running_or_unverified"

    def can_safely_terminate(self, proc: Any | None = None) -> tuple[bool, str]:
        """
        Confirm we hold verified ownership and will not signal a reused PID
        or an unrelated process before issuing terminate/kill.
        """
        if proc is None:
            return False, "no_process_handle"

        exited, basis = self.verify_exited(proc)
        if exited:
            return False, f"already_exited:{basis}"

        handle_exited, _ = self._query_handle_exit()
        if handle_exited is False and self._popen_has_native_handle:
            return True, "native_handle_owned"

        if self.create_time is not None and psutil is not None and self.pid:
            if self.matches_live_process():
                return True, "create_time_verified"
            return False, "pid_identity_mismatch"

        # Unit-test / non-OS fake Popen owned directly by ManagedFFmpegProcess
        if not self._popen_has_native_handle and not isinstance(proc, subprocess.Popen):
            return True, "owned_popen_object"

        if self._popen_has_native_handle:
            return True, "popen_native_handle"

        return False, "identity_unverified"

    def close_handle(self) -> None:
        """Idempotently close our duplicated Win32 handle (never closes Popen._handle)."""
        with self._handle_lock:
            handle = self._native_handle
            owns = self._owns_native_handle
            self._native_handle = None
            self._owns_native_handle = False

        if owns and handle is not None:
            kernel32 = _get_kernel32()
            if kernel32 is not None:
                try:
                    kernel32.CloseHandle(ctypes.c_void_p(handle))
                except Exception:
                    logger.debug("关闭进程查询句柄失败：pid=%s", self.pid, exc_info=True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "create_time": self.create_time,
            "creation_filetime": self.creation_filetime,
            "has_native_handle": self.native_handle is not None,
            "identity_verified": self.identity_verified,
            "verification_basis": self.verification_basis,
            "exited": self.exited,
            "exit_code": self.exit_code,
        }


@runtime_checkable
class ManagedResource(Protocol):
    """Minimal contract for explicitly owned, closeable, verifiable resources."""

    resource_id: str
    resource_type: str
    owner: str

    @property
    def state(self) -> ResourceState: ...

    def request_stop(self, reason: str = "") -> bool: ...

    def close(
        self,
        timeout: float = 5.0,
        *,
        deadline: float | None = None,
        reason: str = "",
    ) -> ResourceCloseResult: ...

    def verify_released(self) -> bool: ...

    def snapshot(self) -> dict[str, Any]: ...


class ResourceTracker:
    """
    Lightweight thread-safe registry for explicitly owned managed resources.

    Responsibilities are strictly limited to:
    - explicit registration / unregistration
    - live resource counts & diagnostics
    - assisting RuntimeServices / owning services in identifying unreleased
      owned resources during shutdown.

    Does NOT create background threads, scan global OS processes, or kill
    unowned processes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resources: dict[str, ManagedResource] = {}

    def register(self, resource: ManagedResource) -> str:
        resource_id = str(getattr(resource, "resource_id", "")).strip()
        if not resource_id:
            raise ValueError("ManagedResource 必须具有非空 resource_id。")
        owner = str(getattr(resource, "owner", "")).strip()
        if not owner:
            raise ValueError("ManagedResource 必须具有明确的 owner。")

        with self._lock:
            existing = self._resources.get(resource_id)
            if existing is not None and existing is not resource:
                raise ValueError(f"资源 ID 已注册：{resource_id}")
            self._resources[resource_id] = resource
        return resource_id

    def unregister(self, resource_id: str) -> bool:
        with self._lock:
            return self._resources.pop(resource_id, None) is not None

    def get(self, resource_id: str) -> ManagedResource | None:
        with self._lock:
            return self._resources.get(resource_id)

    def list_resources(
        self,
        *,
        resource_type: str | None = None,
        owner: str | None = None,
        owner_prefix: str | None = None,
    ) -> list[ManagedResource]:
        with self._lock:
            items = list(self._resources.values())
        result = []
        for resource in items:
            if resource_type is not None and resource.resource_type != resource_type:
                continue
            if owner is not None and resource.owner != owner:
                continue
            if owner_prefix is not None and not resource.owner.startswith(owner_prefix):
                continue
            result.append(resource)
        return result

    def count(
        self,
        *,
        resource_type: str | None = None,
        owner_prefix: str | None = None,
    ) -> int:
        return len(
            self.list_resources(
                resource_type=resource_type,
                owner_prefix=owner_prefix,
            )
        )

    def unreleased_resources(
        self,
        *,
        resource_type: str | None = None,
        owner_prefix: str | None = None,
    ) -> list[ManagedResource]:
        unreleased = []
        for resource in self.list_resources(
            resource_type=resource_type,
            owner_prefix=owner_prefix,
        ):
            try:
                released = bool(resource.verify_released())
            except Exception:
                released = False
            if not released:
                unreleased.append(resource)
        return unreleased

    def has_unreleased_resources(
        self,
        *,
        resource_type: str | None = None,
        owner_prefix: str | None = None,
    ) -> bool:
        return bool(
            self.unreleased_resources(
                resource_type=resource_type,
                owner_prefix=owner_prefix,
            )
        )

    def request_stop_for(
        self,
        *,
        owner_prefix: str | None = None,
        resource_type: str | None = None,
        reason: str = "stop_requested",
        deadline: float | None = None,
    ) -> int:
        signaled = 0
        for resource in self.list_resources(
            resource_type=resource_type,
            owner_prefix=owner_prefix,
        ):
            try:
                if deadline is not None:
                    try:
                        called = resource.request_stop(reason=reason, deadline=deadline)
                    except TypeError:
                        called = resource.request_stop(reason=reason)
                else:
                    called = resource.request_stop(reason=reason)
                if called:
                    signaled += 1
            except Exception:
                logger.exception(
                    "向受管资源发送停止请求失败：%s",
                    resource.resource_id,
                )
        return signaled

    def close_owned_resources(
        self,
        *,
        owner_prefix: str | None = None,
        resource_type: str | None = None,
        timeout: float = 5.0,
        deadline: float | None = None,
        reason: str = "shutdown",
    ) -> list[ResourceCloseResult]:
        """
        Ask explicitly registered resources matching owner_prefix/type to close
        within a shared deadline. Only operates on tracked resources.
        """
        close_deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + max(0.0, float(timeout))
        )
        results: list[ResourceCloseResult] = []
        for resource in self.list_resources(
            resource_type=resource_type,
            owner_prefix=owner_prefix,
        ):
            remaining = max(0.0, close_deadline - time.monotonic())
            try:
                result = resource.close(
                    timeout=remaining,
                    deadline=close_deadline,
                    reason=reason,
                )
            except Exception as exc:
                result = ResourceCloseResult(
                    resource_id=resource.resource_id,
                    resource_type=resource.resource_type,
                    owner=resource.owner,
                    state=ResourceState.STOP_FAILED,
                    closed=False,
                    exit_verified=False,
                    reason=reason,
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
        return results

    def diagnostics(
        self,
        *,
        resource_type: str | None = None,
        owner_prefix: str | None = None,
    ) -> dict[str, Any]:
        resources = self.list_resources(
            resource_type=resource_type,
            owner_prefix=owner_prefix,
        )
        by_type: dict[str, int] = {}
        by_state: dict[str, int] = {}
        remaining_snapshots: list[dict[str, Any]] = []
        unreleased_count = 0

        for resource in resources:
            by_type[resource.resource_type] = by_type.get(resource.resource_type, 0) + 1
            state_value = resource.state.value
            by_state[state_value] = by_state.get(state_value, 0) + 1
            try:
                released = bool(resource.verify_released())
            except Exception:
                released = False
            if not released:
                unreleased_count += 1
            try:
                snap = resource.snapshot()
            except Exception as exc:
                snap = {
                    "resource_id": resource.resource_id,
                    "resource_type": resource.resource_type,
                    "owner": resource.owner,
                    "state": state_value,
                    "snapshot_error": f"{type(exc).__name__}: {exc}",
                }
            snap["released"] = released
            remaining_snapshots.append(snap)

        return {
            "registered_count": len(resources),
            "unreleased_count": unreleased_count,
            "by_type": by_type,
            "by_state": by_state,
            "remaining": remaining_snapshots,
        }
