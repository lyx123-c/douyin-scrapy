from fastapi import Request

from backend.services.capture_service import CaptureService
from backend.services.download_queue_service import DownloadQueueService
from backend.services.runtime import RuntimeServices
from backend.services.task_manager import TaskManager
from backend.services.video_query_service import VideoQueryService


def get_runtime_services(request: Request) -> RuntimeServices:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, RuntimeServices):
        raise RuntimeError("RuntimeServices 尚未初始化。")
    return runtime


def get_task_manager(request: Request) -> TaskManager:
    """Return the TaskManager owned by the FastAPI runtime."""
    return get_runtime_services(request).task_manager


def get_video_query_service(request: Request) -> VideoQueryService:
    return VideoQueryService(get_runtime_services(request))


def get_capture_service(request: Request) -> CaptureService:
    return get_runtime_services(request).capture_service


def get_download_service(request: Request) -> DownloadQueueService:
    return get_runtime_services(request).download_queue_service

