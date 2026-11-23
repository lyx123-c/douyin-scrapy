import logging
import threading
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from config import DOWNLOAD_QUEUE_JSON
from backend.services.capture_service import CaptureService
from backend.services.download_queue_service import DownloadQueueService
from backend.services.resource_lifecycle import ResourceTracker
from backend.services.task_manager import TaskManager


logger = logging.getLogger(__name__)

DEFAULT_QUEUE_STORE = Path(DOWNLOAD_QUEUE_JSON)


class RuntimeShutdownState(str, Enum):
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    STOP_FAILED = "stop_failed"


class RuntimeWorkUnavailableError(RuntimeError):
    """Raised when startup is attempted after Runtime shutdown began."""


class _ShutdownRound:
    """Per-round coordination state so retries never reuse or clear an existing Event."""

    def __init__(self, round_id: int, owner_thread_id: int) -> None:
        self.round_id = round_id
        self.owner_thread_id = owner_thread_id
        self.done = threading.Event()
        self.success: bool = False
        self.outcome: str = "in_progress"
        self.report: dict = {}


class RuntimeServices:
    """Backend runtime container with no automatic background work."""

    def __init__(
        self,
        *,
        task_manager: TaskManager | None = None,
        store_path: str | Path | None = None,
        capture_service: CaptureService | None = None,
        download_queue_service: DownloadQueueService | None = None,
        resource_tracker: ResourceTracker | None = None,
    ) -> None:
        if task_manager is not None and store_path is not None:
            raise ValueError("task_manager 与 store_path 不能同时提供。")

        if task_manager is None:
            task_manager = TaskManager(
                store_path=str(store_path or DEFAULT_QUEUE_STORE)
            )

        self.task_manager = task_manager
        self.resource_tracker = resource_tracker or ResourceTracker()
        self.capture_service = capture_service or CaptureService(
            task_manager,
            work_gate=self.work_lease,
        )
        self.download_queue_service = (
            download_queue_service
            or DownloadQueueService(
                task_manager,
                work_gate=self.work_lease,
                resource_tracker=self.resource_tracker,
            )
        )

        if self.capture_service.task_manager is not task_manager:
            raise ValueError("CaptureService 必须使用 RuntimeServices 的 TaskManager。")
        if self.download_queue_service.task_manager is not task_manager:
            raise ValueError("DownloadQueueService 必须使用 RuntimeServices 的 TaskManager。")

        self._shutdown_lock = threading.Lock()
        self._work_condition = threading.Condition(self._shutdown_lock)
        self._active_work_starts = 0
        self._shutdown_state = RuntimeShutdownState.RUNNING
        self._shutdown_report: dict = {}
        self._active_shutdown: _ShutdownRound | None = None
        self._shutdown_round_seq: int = 0
        self._confirmed_stopped_services: set[str] = set()

    @property
    def is_shutdown(self) -> bool:
        with self._shutdown_lock:
            return self._shutdown_state == RuntimeShutdownState.STOPPED

    @property
    def is_accepting_work(self) -> bool:
        with self._shutdown_lock:
            return self._shutdown_state == RuntimeShutdownState.RUNNING

    @property
    def shutdown_state(self) -> RuntimeShutdownState:
        with self._shutdown_lock:
            return self._shutdown_state

    @property
    def shutdown_report(self) -> dict:
        with self._shutdown_lock:
            return dict(self._shutdown_report)

    def _remaining_time(self, deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    @property
    def active_work_starts(self) -> int:
        with self._shutdown_lock:
            return self._active_work_starts

    def ensure_accepting_work(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_state != RuntimeShutdownState.RUNNING:
                raise RuntimeWorkUnavailableError(
                    "Runtime 正在关闭或已经关闭，不能启动新工作。"
                )

    @contextmanager
    def work_lease(self):
        with self._work_condition:
            if self._shutdown_state != RuntimeShutdownState.RUNNING:
                raise RuntimeWorkUnavailableError(
                    "Runtime 正在关闭或已经关闭，不能启动新工作。"
                )
            self._active_work_starts += 1

        try:
            yield
        finally:
            with self._work_condition:
                self._active_work_starts = max(
                    0,
                    self._active_work_starts - 1,
                )
                if self._active_work_starts == 0:
                    self._work_condition.notify_all()

    def _wait_for_work_starts(self, deadline: float) -> bool:
        with self._work_condition:
            while self._active_work_starts > 0:
                remaining = self._remaining_time(deadline)
                if remaining <= 0:
                    return False
                self._work_condition.wait(remaining)
            return True

    def _close_service(
        self,
        service,
        deadline: float,
    ) -> dict:
        result = {
            "shutdown_returned": None,
            "confirmed_stopped": False,
            "running_after_shutdown": True,
            "error": None,
            "success": False,
            "skipped": False,
        }

        try:
            returned = service.shutdown(
                timeout=self._remaining_time(deadline)
            )
            result["shutdown_returned"] = bool(returned)
        except Exception as exc:
            result["shutdown_returned"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"

        try:
            running = bool(service.is_running())
        except Exception as exc:
            running = True
            state_error = f"{type(exc).__name__}: {exc}"
            result["error"] = (
                f"{result['error']}; state check: {state_error}"
                if result["error"]
                else f"state check: {state_error}"
            )

        result["running_after_shutdown"] = running
        result["confirmed_stopped"] = not running
        result["success"] = (
            result["error"] is None
            and result["shutdown_returned"] is True
            and not running
        )
        return result

    def _close_or_recheck_service(
        self,
        key: str,
        service,
        deadline: float,
        previously_confirmed: set[str],
    ) -> dict:
        if key in previously_confirmed:
            try:
                running = bool(service.is_running())
            except Exception:
                return self._close_service(service, deadline)
            if not running:
                return {
                    "shutdown_returned": True,
                    "confirmed_stopped": True,
                    "running_after_shutdown": False,
                    "error": None,
                    "success": True,
                    "skipped": True,
                }
        return self._close_service(service, deadline)

    def _classify_outcome(self, report: dict, success: bool) -> str:
        if success:
            return "stopped"
        has_error = bool(
            report.get("error")
            or report.get("capture", {}).get("error")
            or report.get("download", {}).get("error")
            or report.get("task_manager", {}).get("error")
        )
        if has_error:
            return "error"
        if report.get("deadline_exceeded"):
            return "timeout"
        return "failed"

    def shutdown(self, timeout: float = 30.0) -> bool:
        total_timeout = max(0.0, float(timeout))
        started_at = time.monotonic()
        deadline = started_at + total_timeout
        current_thread_id = threading.get_ident()

        with self._shutdown_lock:
            state = self._shutdown_state
            if state == RuntimeShutdownState.STOPPED:
                return True
            if state == RuntimeShutdownState.STOPPING:
                current_round = self._active_shutdown
                if current_round is not None and current_round.owner_thread_id == current_thread_id:
                    logger.warning(
                        "Re-entrant RuntimeServices.shutdown() detected on owner thread (round=%s).",
                        current_round.round_id,
                    )
                    return False
                owner = False
                previously_confirmed: set[str] = set()
            else:
                self._shutdown_round_seq += 1
                current_round = _ShutdownRound(
                    round_id=self._shutdown_round_seq,
                    owner_thread_id=current_thread_id,
                )
                self._shutdown_state = RuntimeShutdownState.STOPPING
                self._shutdown_report = {}
                self._active_shutdown = current_round
                previously_confirmed = set(self._confirmed_stopped_services)
                owner = True

        if not owner:
            if current_round is None:
                return self.is_shutdown
            completed = current_round.done.wait(self._remaining_time(deadline))
            if not completed:
                logger.warning(
                    "RuntimeServices.shutdown() waiter timed out (round=%s, timeout=%.3fs).",
                    current_round.round_id,
                    total_timeout,
                )
                return False
            return current_round.success

        report = {
            "round_id": current_round.round_id,
            "success": False,
            "outcome": "in_progress",
            "state": RuntimeShutdownState.STOPPING.value,
            "timeout": total_timeout,
            "elapsed_seconds": 0.0,
            "deadline_exceeded": False,
            "work_starts_drained": False,
            "capture": {},
            "download": {},
            "resources": {},
            "task_manager": {
                "saved": False,
                "error": None,
            },
            "error": None,
        }

        success = False
        final_state = RuntimeShutdownState.STOP_FAILED
        outcome = "error"

        try:
            report["work_starts_drained"] = self._wait_for_work_starts(
                deadline
            )
            report["capture"] = self._close_or_recheck_service(
                "capture",
                self.capture_service,
                deadline,
                previously_confirmed,
            )
            report["download"] = self._close_or_recheck_service(
                "download",
                self.download_queue_service,
                deadline,
                previously_confirmed,
            )

            for key, service in (
                ("capture", self.capture_service),
                ("download", self.download_queue_service),
            ):
                service_result = report[key]
                try:
                    running = bool(service.is_running())
                except Exception as exc:
                    running = True
                    state_error = f"{type(exc).__name__}: {exc}"
                    service_result["error"] = (
                        f"{service_result['error']}; final state check: {state_error}"
                        if service_result["error"]
                        else f"final state check: {state_error}"
                    )
                service_result["running_after_shutdown"] = running
                service_result["confirmed_stopped"] = not running
                service_result["success"] = (
                    service_result["error"] is None
                    and service_result["shutdown_returned"] is True
                    and not running
                )

            report["resources"] = self.resource_tracker.diagnostics()

            try:
                report["task_manager"]["saved"] = bool(
                    self.task_manager.save()
                )
            except Exception as exc:
                report["task_manager"]["error"] = (
                    f"{type(exc).__name__}: {exc}"
                )

        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                elapsed = max(0.0, time.monotonic() - started_at)
                report["elapsed_seconds"] = elapsed
                report["deadline_exceeded"] = elapsed >= total_timeout
                all_threads_stopped = (
                    report["capture"].get("running_after_shutdown") is False
                    and report["download"].get("running_after_shutdown") is False
                )
                all_resources_released = (
                    report["resources"].get("unreleased_count", 0) == 0
                )
                success = bool(
                    report["error"] is None
                    and report["work_starts_drained"] is True
                    and report["capture"].get("success") is True
                    and report["download"].get("success") is True
                    and all_threads_stopped
                    and all_resources_released
                    and report["task_manager"]["saved"] is True
                    and report["task_manager"]["error"] is None
                    and not report["deadline_exceeded"]
                )
                final_state = (
                    RuntimeShutdownState.STOPPED
                    if success
                    else RuntimeShutdownState.STOP_FAILED
                )
                outcome = self._classify_outcome(report, success)
                report["success"] = success
                report["outcome"] = outcome
                report["state"] = final_state.value
                report["all_threads_stopped"] = all_threads_stopped
                report["all_resources_released"] = all_resources_released
            finally:
                with self._shutdown_lock:
                    for key in ("capture", "download"):
                        if report.get(key, {}).get("confirmed_stopped") is True:
                            self._confirmed_stopped_services.add(key)
                        else:
                            self._confirmed_stopped_services.discard(key)
                    self._shutdown_state = final_state
                    self._shutdown_report = dict(report)
                    if self._active_shutdown is current_round:
                        self._active_shutdown = None
                    current_round.success = success
                    current_round.outcome = outcome
                    current_round.report = dict(report)
                    current_round.done.set()

            if success:
                logger.info(
                    "RuntimeServices shutdown succeeded (round=%s, elapsed=%.3fs).",
                    current_round.round_id,
                    report["elapsed_seconds"],
                )
            else:
                logger.warning(
                    "RuntimeServices shutdown did not complete cleanly (round=%s, outcome=%s, state=%s, elapsed=%.3fs).",
                    current_round.round_id,
                    outcome,
                    final_state.value,
                    report["elapsed_seconds"],
                )

        return success

