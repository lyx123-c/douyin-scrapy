import inspect
import logging
import time
import threading
from contextlib import nullcontext
from typing import Callable

from backend.repositories import is_already_downloaded
from backend.services.downloader import DownloadTaskResult, download_video_task
from backend.services.resource_lifecycle import ResourceTracker
from backend.services.task_manager import TaskManager
from config import AWEME_ID_RE, SAVE_DIR


logger = logging.getLogger(__name__)


class DownloadQueueService:
    """Own the lifecycle of the single sequential download worker."""

    def __init__(
        self,
        task_manager: TaskManager,
        *,
        work_gate: Callable[[], object] | None = None,
        resource_tracker: ResourceTracker | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._work_gate = work_gate or nullcontext
        self._resource_tracker = resource_tracker
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._active_task_id: str | None = None
        self._active_task_stop_event: threading.Event | None = None
        self._thread_lock = threading.Lock()
        self._force_task_ids: set[str] = set()
        self._shutdown_started = False
        self._shutdown_deadline: float | None = None

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    def start(self) -> bool:
        with self._work_gate():
            return self._start()

    def _start(self) -> bool:
        with self._thread_lock:
            if self._shutdown_started:
                return False
            if self._thread is not None and self._thread.is_alive():
                return False
            if not self._task_manager.start_download_queue():
                return False

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._worker_loop,
                args=(stop_event,),
                name="douyin-download-queue",
                daemon=True,
            )
            self._thread = thread
            self._stop_event = stop_event

            try:
                thread.start()
            except Exception as exc:
                logger.exception("下载队列线程启动失败。")
                self._thread = None
                self._stop_event = None
                stop_event.set()
                self._task_manager.finish_download_queue()
                raise RuntimeError(f"下载队列线程启动失败：{exc}") from exc

        return True

    def set_force_download(self, task_id: str, force: bool = True) -> None:
        task_id = str(task_id).strip()
        if not task_id:
            return
        with self._thread_lock:
            if force:
                self._force_task_ids.add(task_id)
            else:
                self._force_task_ids.discard(task_id)

    def _worker_loop(self, stop_event: threading.Event) -> None:
        from backend.services.runtime import RuntimeWorkUnavailableError

        try:
            while not stop_event.is_set():
                try:
                    with self._work_gate():
                        task = self._task_manager.next_waiting_download()
                except RuntimeWorkUnavailableError:
                    break
                if task is None:
                    break

                try:
                    task_stop_event = threading.Event()
                    with self._thread_lock:
                        force = task["task_id"] in self._force_task_ids
                        self._active_task_id = task["task_id"]
                        self._active_task_stop_event = task_stop_event
                        if stop_event.is_set():
                            task_stop_event.set()

                    if not force and is_already_downloaded(task["video_id"]):
                        self._task_manager.mark_skipped(
                            task["task_id"],
                            message="已存在",
                        )
                        continue

                    def report_progress(payload):
                        self._task_manager.report_progress(
                            task["task_id"],
                            payload,
                        )

                    outcome = self._invoke_download_task(
                        task,
                        force=force,
                        report_progress=report_progress,
                        stop_event=task_stop_event,
                    )
                    if isinstance(outcome, DownloadTaskResult):
                        if outcome.status == "success":
                            self._task_manager.mark_success(
                                task["task_id"],
                                message=outcome.message,
                            )
                        elif outcome.status == "interrupted":
                            self._task_manager.update_task(
                                task["task_id"],
                                status="interrupted",
                                message=outcome.message,
                                error=None,
                            )
                        elif outcome.status == "skipped":
                            self._task_manager.mark_skipped(
                                task["task_id"],
                                message=outcome.message,
                            )
                        else:
                            self._task_manager.mark_failed(
                                task["task_id"],
                                error=outcome.error or outcome.message,
                            )
                    else:
                        success, message = outcome
                        if success:
                            self._task_manager.mark_success(
                                task["task_id"],
                                message=message,
                            )
                        else:
                            self._task_manager.mark_failed(
                                task["task_id"],
                                error=message,
                            )
                except Exception as exc:
                    logger.exception("下载任务执行异常：%s", task.get("task_id"))
                    self._task_manager.mark_failed(
                        task["task_id"],
                        error=str(exc),
                    )
                finally:
                    with self._thread_lock:
                        self._force_task_ids.discard(task["task_id"])
                        if self._active_task_id == task["task_id"]:
                            self._active_task_id = None
                            self._active_task_stop_event = None
        except Exception:
            logger.exception("下载队列 Worker 意外退出。")
        finally:
            self._task_manager.finish_download_queue()
            current_thread = threading.current_thread()
            with self._thread_lock:
                if self._thread is current_thread:
                    self._thread = None
                    self._stop_event = None

    def _invoke_download_task(
        self,
        task: dict,
        *,
        force: bool,
        report_progress: Callable[[dict], None],
        stop_event: threading.Event,
    ):
        with self._thread_lock:
            shutdown_deadline = self._shutdown_deadline

        kwargs: dict = {
            "force": force,
            "progress_callback": report_progress,
            "work_gate": self._work_gate,
        }
        optional_kwargs = {
            "stop_event": stop_event,
            "resource_tracker": self._resource_tracker,
            "deadline": shutdown_deadline,
            "owner_id": f"download:{task['task_id']}",
            "save_dir": task.get("target_dir") or SAVE_DIR,
            "return_details": True,
        }

        try:
            inspect_target = download_video_task
            side_effect = getattr(download_video_task, "side_effect", None)
            if callable(side_effect):
                inspect_target = side_effect
            signature = inspect.signature(inspect_target)
            accepts_var_kw = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in signature.parameters.values()
            )
            for key, value in optional_kwargs.items():
                if accepts_var_kw or key in signature.parameters:
                    kwargs[key] = value
        except (TypeError, ValueError):
            pass

        return download_video_task(task["video_id"], **kwargs)

    def _has_unreleased_ffmpeg(self) -> bool:
        if self._resource_tracker is None:
            return False
        return self._resource_tracker.has_unreleased_resources(
            owner_prefix="download:",
            resource_type="ffmpeg",
        )

    def request_stop(self) -> bool:
        with self._thread_lock:
            stop_event = self._stop_event
            task_stop_event = self._active_task_stop_event
            thread = self._thread
            shutdown_deadline = self._shutdown_deadline

        if task_stop_event is not None:
            task_stop_event.set()

        if self._resource_tracker is not None:
            self._resource_tracker.request_stop_for(
                owner_prefix="download:",
                resource_type="ffmpeg",
                reason="download_stop_requested",
                deadline=shutdown_deadline,
            )

        if stop_event is None or stop_event.is_set():
            return False

        stop_event.set()
        return bool(thread and thread.is_alive())

    def cancel_task(self, task_id: str) -> dict:
        task_id = str(task_id).strip()
        task = self._task_manager.get_task(task_id)
        if task is None:
            return {"cancelled": False, "reason": "not_found", "task": None}

        status = task.get("status")
        if status == "waiting":
            updated = self._task_manager.update_task(
                task_id,
                status="interrupted",
                message="任务已取消，可点击重试",
                error=None,
            )
            self.set_force_download(task_id, False)
            return {
                "cancelled": True,
                "reason": "cancelled_waiting",
                "task": updated,
            }

        if status != "running":
            return {"cancelled": False, "reason": "not_cancellable", "task": task}

        with self._thread_lock:
            active_id = self._active_task_id
            task_stop_event = self._active_task_stop_event

        if active_id == task_id and task_stop_event is not None:
            task_stop_event.set()
            if self._resource_tracker is not None:
                self._resource_tracker.request_stop_for(
                    owner_prefix=f"download:{task_id}",
                    resource_type="ffmpeg",
                    reason="task_cancelled",
                )
            return {
                "cancelled": True,
                "reason": "cancelling_running",
                "task": task,
            }

        return {"cancelled": False, "reason": "not_active", "task": task}

    def get_task(self, task_id: str) -> dict | None:
        return self._task_manager.get_task(task_id)

    def snapshot(self) -> dict:
        tasks = self._task_manager.get_tasks()
        return {
            "items": tasks,
            "total": len(tasks),
            "counts": self._task_manager.counts(),
            "queue": self.get_status(),
        }

    def queue_items(self, items: list[dict]) -> dict:
        with self._work_gate():
            created = []
            existing = []
            skipped = []
            for item in items:
                video_id = str(item.get("video_id", "")).strip()
                if not AWEME_ID_RE.fullmatch(video_id):
                    skipped.append(video_id)
                    continue
                title = str(item.get("title", "")).strip() or "无标题"
                task, was_created = self._task_manager.enqueue_download(
                    video_id,
                    title,
                    save_dir=SAVE_DIR,
                )
                if was_created:
                    created.append(task)
                else:
                    existing.append(task)
            return {
                "created": created,
                "existing": existing,
                "skipped": skipped,
                "created_count": len(created),
            }

    def queue_immediate(self, video_id: str, title: str, force: bool) -> dict:
        with self._work_gate():
            return self._queue_immediate(video_id, title, force)

    def _queue_immediate(self, video_id: str, title: str, force: bool) -> dict:
        video_id = str(video_id).strip()
        if not AWEME_ID_RE.fullmatch(video_id):
            raise ValueError("视频 ID 格式不正确。")

        task = self._task_manager.find_by_video_id(video_id)
        if task is None:
            task, _ = self._task_manager.enqueue_download(
                video_id,
                str(title).strip() or "无标题",
                save_dir=SAVE_DIR,
            )
        else:
            status = task.get("status")
            if status == "running":
                return {"queued": False, "reason": "running", "task": task}
            if status in ("failed", "interrupted"):
                task = self._task_manager.retry_task(task["task_id"])
            elif status in ("success", "skipped"):
                if not force:
                    return {"queued": False, "reason": "already_downloaded", "task": task}
                task = self._task_manager.update_task(
                    task["task_id"],
                    status="waiting",
                    progress=0.0,
                    message="等待强制重新下载",
                    error=None,
                )

        self.set_force_download(task["task_id"], force)
        started = self._start()
        if not started and not self.is_running():
            self.set_force_download(task["task_id"], False)
            return {"queued": False, "reason": "queue_unavailable", "task": task}

        return {"queued": True, "reason": "queued", "task": task}

    def retry_task(self, task_id: str) -> dict | None:
        with self._work_gate():
            task = self._task_manager.retry_task(task_id)
            if task is not None:
                self._start()
            return task

    def remove_task(self, task_id: str) -> dict:
        task = self._task_manager.get_task(task_id)
        if task is None:
            return {"removed": False, "reason": "not_found"}
        if task.get("status") == "running":
            return {"removed": False, "reason": "running"}
        removed = self._task_manager.remove_task(task_id)
        self.set_force_download(task_id, False)
        return {"removed": removed, "reason": "removed" if removed else "not_found"}

    def clear_completed(self) -> int:
        tasks = self._task_manager.get_tasks()
        for task in tasks:
            self.set_force_download(task["task_id"], False)
        return self._task_manager.clear_completed()

    def get_status(self) -> dict:
        with self._thread_lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            stopping = bool(self._stop_event and self._stop_event.is_set())
            forced_task_count = len(self._force_task_ids)
        active_ffmpeg_count = (
            self._resource_tracker.count(
                owner_prefix="download:",
                resource_type="ffmpeg",
            )
            if self._resource_tracker is not None
            else 0
        )
        return {
            "running": thread_alive or self._task_manager.is_download_running(),
            "thread_alive": thread_alive,
            "stopping": stopping,
            "forced_task_count": forced_task_count,
            "active_ffmpeg_count": active_ffmpeg_count,
            "counts": self._task_manager.counts(),
        }

    def is_running(self) -> bool:
        with self._thread_lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
        return (
            thread_alive
            or self._task_manager.is_download_running()
            or self._has_unreleased_ffmpeg()
        )

    def shutdown(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))

        with self._thread_lock:
            self._shutdown_started = True
            self._shutdown_deadline = deadline

        self.request_stop()

        with self._thread_lock:
            thread = self._thread

        if thread is not None and thread.is_alive():
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

        if (
            self._resource_tracker is not None
            and self._resource_tracker.count(
                owner_prefix="download:",
                resource_type="ffmpeg",
            )
            > 0
        ):
            self._resource_tracker.close_owned_resources(
                owner_prefix="download:",
                resource_type="ffmpeg",
                deadline=deadline,
                reason="download_shutdown",
            )

        thread_stopped = thread is None or not thread.is_alive()
        return thread_stopped and not self._has_unreleased_ffmpeg()

