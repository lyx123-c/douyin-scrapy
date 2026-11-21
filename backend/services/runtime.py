from pathlib import Path

from config import DOWNLOAD_QUEUE_JSON
from backend.services.capture_service import CaptureService
from backend.services.download_queue_service import DownloadQueueService
from backend.services.task_manager import TaskManager


DEFAULT_QUEUE_STORE = Path(DOWNLOAD_QUEUE_JSON)


class RuntimeServices:
    """Backend runtime container with no automatic background work."""

    def __init__(
        self,
        *,
        task_manager: TaskManager | None = None,
        store_path: str | Path | None = None,
        capture_service: CaptureService | None = None,
        download_queue_service: DownloadQueueService | None = None,
    ) -> None:
        if task_manager is not None and store_path is not None:
            raise ValueError("task_manager 与 store_path 不能同时提供。")

        if task_manager is None:
            task_manager = TaskManager(
                store_path=str(store_path or DEFAULT_QUEUE_STORE)
            )

        self.task_manager = task_manager
        self.capture_service = capture_service or CaptureService(task_manager)
        self.download_queue_service = (
            download_queue_service or DownloadQueueService(task_manager)
        )

        if self.capture_service.task_manager is not task_manager:
            raise ValueError("CaptureService 必须使用 RuntimeServices 的 TaskManager。")
        if self.download_queue_service.task_manager is not task_manager:
            raise ValueError("DownloadQueueService 必须使用 RuntimeServices 的 TaskManager。")

        self._shutdown = False

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown

    def shutdown(self, timeout: float = 30.0) -> bool:
        if self._shutdown:
            return True

        capture_stopped = self.capture_service.shutdown(timeout=timeout)
        download_stopped = self.download_queue_service.shutdown(timeout=timeout)
        stopped = capture_stopped and download_stopped

        if stopped:
            self.task_manager.save()

        self._shutdown = stopped
        return stopped

