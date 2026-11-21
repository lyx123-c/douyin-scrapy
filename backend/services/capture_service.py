import logging
import time
import threading
from typing import Callable

from backend.services.douyin_scraper import (
    fetch_ids_task_generator,
    kill_scraper_browsers_only,
)
from backend.services.task_manager import TaskManager
from config import CAPTURE_STOP_GRACE_SECONDS


logger = logging.getLogger(__name__)


class CaptureService:
    """Own the lifecycle of the existing Scrapling capture thread."""

    def __init__(
        self,
        task_manager: TaskManager,
        *,
        stop_grace_seconds: float = CAPTURE_STOP_GRACE_SECONDS,
        kill_browser: Callable[[], object] = kill_scraper_browsers_only,
    ) -> None:
        self._task_manager = task_manager
        self._stop_grace_seconds = max(0.0, float(stop_grace_seconds))
        self._kill_browser = kill_browser
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._kill_timer: threading.Timer | None = None
        self._shutdown_started = False

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    def start_capture(self, target_url: str, mode: str) -> str | None:
        target_url = str(target_url).strip()
        mode = str(mode).strip()
        if not target_url:
            raise ValueError("抓取地址不能为空。")
        if not mode:
            raise ValueError("抓取模式不能为空。")

        with self._thread_lock:
            if self._shutdown_started:
                return None
            if self._thread is not None and self._thread.is_alive():
                return None
            if self._task_manager.is_capture_running():
                return None
            if self._task_manager.is_download_running():
                return None

            try:
                self._kill_browser()
            except Exception:
                logger.exception("启动抓取前清理残留浏览器失败。")

            capture_task_id = self._task_manager.start_capture()
            if capture_task_id is None:
                return None

            thread = threading.Thread(
                target=self._run_capture,
                args=(capture_task_id, target_url, mode),
                name=f"douyin-capture-{capture_task_id[:8]}",
                daemon=True,
            )
            self._thread = thread
            self._task_manager.set_capture_thread(thread)

            try:
                thread.start()
            except Exception as exc:
                logger.exception("抓取线程启动失败：%s", capture_task_id)
                self._thread = None
                self._task_manager.set_capture_thread(None)
                self._task_manager.finish_capture(
                    results=[],
                    error=f"抓取线程启动失败：{exc}",
                )
                raise RuntimeError(f"抓取线程启动失败：{exc}") from exc

        return capture_task_id

    def _run_capture(self, capture_task_id: str, target_url: str, mode: str) -> None:
        results = []
        error = None
        try:
            generator = fetch_ids_task_generator(
                target_url,
                mode,
                self._task_manager.capture_stop_requested,
                progress_callback=self._task_manager.update_capture_progress,
            )
            results = next(generator)
        except Exception as exc:
            error = str(exc)
            logger.exception("抓取任务异常退出：%s", capture_task_id)
        finally:
            self._task_manager.finish_capture(results, error)
            self._task_manager.set_capture_thread(None)
            with self._thread_lock:
                if self._kill_timer is not None:
                    self._kill_timer.cancel()
                    self._kill_timer = None
                if self._thread is threading.current_thread():
                    self._thread = None

    def request_stop(self) -> bool:
        requested = self._task_manager.request_capture_stop()
        if not requested:
            return False

        snapshot = self._task_manager.get_capture_snapshot()
        if snapshot is None or self._stop_grace_seconds <= 0:
            return True

        timer = threading.Timer(
            self._stop_grace_seconds,
            self._kill_browser_if_still_running,
            args=(snapshot["task_id"],),
        )
        timer.daemon = True

        with self._thread_lock:
            if self._kill_timer is not None:
                self._kill_timer.cancel()
            self._kill_timer = timer

        try:
            timer.start()
        except Exception:
            logger.exception("抓取停止监督线程启动失败。")
            timer.cancel()
            with self._thread_lock:
                if self._kill_timer is timer:
                    self._kill_timer = None

        return True

    def _kill_browser_if_still_running(self, capture_task_id: str) -> None:
        snapshot = self._task_manager.get_capture_snapshot()
        if (
            snapshot
            and snapshot.get("task_id") == capture_task_id
            and snapshot.get("status") == "running"
        ):
            logger.warning("抓取停止宽限期已到，关闭 Scrapling 浏览器：%s", capture_task_id)
            try:
                self._kill_browser()
            except Exception:
                logger.exception("停止宽限期结束后关闭抓取浏览器失败。")

    def get_status(self) -> dict | None:
        return self._task_manager.get_capture_snapshot()

    def get_public_status(self) -> dict:
        snapshot = self.get_status()
        if snapshot is None:
            return {
                "task_id": None,
                "status": "idle",
                "message": "",
                "progress": {},
                "error": None,
                "stopping": False,
                "stop_requested_at": None,
            }
        return {
            "task_id": snapshot.get("task_id"),
            "status": snapshot.get("status", "idle"),
            "message": snapshot.get("message", ""),
            "progress": snapshot.get("progress") or {},
            "error": snapshot.get("error"),
            "stopping": bool(snapshot.get("stopping")),
            "stop_requested_at": snapshot.get("stop_requested_at"),
        }

    def get_results(self) -> dict:
        snapshot = self.get_status()
        if snapshot is None:
            return {"task_id": None, "items": [], "total": 0}
        items = [
            {
                "video_id": str(item.get("video_id", item.get("视频ID", ""))).strip(),
                "title": str(item.get("title", item.get("标题", "无标题"))).strip() or "无标题",
            }
            for item in snapshot.get("results", [])
            if str(item.get("video_id", item.get("视频ID", ""))).strip()
        ]
        return {
            "task_id": snapshot.get("task_id"),
            "items": items,
            "total": len(items),
        }

    def clear_status(self) -> None:
        self._task_manager.clear_capture()

    def is_running(self) -> bool:
        with self._thread_lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
        return thread_alive or self._task_manager.is_capture_running()

    def shutdown(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))

        with self._thread_lock:
            self._shutdown_started = True

        if self.is_running():
            self.request_stop()

        with self._thread_lock:
            thread = self._thread
            timer = self._kill_timer

        if thread is not None and thread.is_alive():
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

        finished = thread is None or not thread.is_alive()
        if finished and timer is not None:
            timer.cancel()

        return finished

