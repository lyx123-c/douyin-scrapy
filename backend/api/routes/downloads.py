from fastapi import APIRouter, Depends

from backend.services.download_queue_service import DownloadQueueService
from backend.services.runtime import (
    RuntimeServices,
    RuntimeWorkUnavailableError,
)

from ..dependencies import get_download_service, get_runtime_services
from ..errors import BusinessError
from ..schemas import (
    ApiResponse,
    DownloadBatchRequest,
    DownloadBatchResult,
    DownloadClearData,
    DownloadDeleteData,
    DownloadImmediateResult,
    DownloadQueueState,
    DownloadSnapshotData,
    DownloadStartData,
    DownloadStopData,
    DownloadTaskItem,
    ImmediateDownloadRequest,
)


router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.get("", response_model=ApiResponse[DownloadSnapshotData])
def downloads_snapshot(
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadSnapshotData]:
    return ApiResponse[DownloadSnapshotData](
        data=DownloadSnapshotData.model_validate(service.snapshot()),
        message="下载队列数据",
    )


@router.get("/status", response_model=ApiResponse[DownloadQueueState])
def download_status(
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadQueueState]:
    return ApiResponse[DownloadQueueState](
        data=DownloadQueueState.model_validate(service.get_status()),
        message="下载队列状态",
    )


@router.post("/start", response_model=ApiResponse[DownloadStartData])
def download_start(
    runtime: RuntimeServices = Depends(get_runtime_services),
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadStartData]:
    try:
        with runtime.work_lease():
            started = service.start()
    except RuntimeWorkUnavailableError as exc:
        raise BusinessError(
            str(exc),
            status_code=503,
            code="RUNTIME_NOT_ACCEPTING_WORK",
        ) from exc
    running = service.is_running()
    if not started and not running:
        raise BusinessError("下载队列启动失败。", status_code=503, code="DOWNLOAD_START_FAILED")
    return ApiResponse[DownloadStartData](
        data=DownloadStartData(
            started=started,
            running=running,
            queue=DownloadQueueState.model_validate(service.get_status()),
        ),
        message="下载队列已启动" if started else "下载队列已经在运行",
    )


@router.post("/stop", response_model=ApiResponse[DownloadStopData])
def download_stop(
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadStopData]:
    if not service.request_stop():
        raise BusinessError(
            "下载队列当前未运行，或已经在停止中。",
            status_code=409,
            code="DOWNLOAD_NOT_RUNNING",
        )
    return ApiResponse[DownloadStopData](
        data=DownloadStopData(stopping=True),
        message="将在当前任务完成后停止下载队列",
    )


@router.post("/tasks", response_model=ApiResponse[DownloadBatchResult])
def add_download_tasks(
    payload: DownloadBatchRequest,
    runtime: RuntimeServices = Depends(get_runtime_services),
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadBatchResult]:
    try:
        with runtime.work_lease():
            result = service.queue_items(
                [item.model_dump() for item in payload.items]
            )
    except RuntimeWorkUnavailableError as exc:
        raise BusinessError(
            str(exc),
            status_code=503,
            code="RUNTIME_NOT_ACCEPTING_WORK",
        ) from exc
    return ApiResponse[DownloadBatchResult](
        data=DownloadBatchResult.model_validate(result),
        message="下载任务已加入队列",
    )


@router.post("/immediate", response_model=ApiResponse[DownloadImmediateResult])
def immediate_download(
    payload: ImmediateDownloadRequest,
    runtime: RuntimeServices = Depends(get_runtime_services),
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadImmediateResult]:
    try:
        with runtime.work_lease():
            result = service.queue_immediate(
                payload.video_id,
                payload.title,
                payload.force,
            )
    except RuntimeWorkUnavailableError as exc:
        raise BusinessError(
            str(exc),
            status_code=503,
            code="RUNTIME_NOT_ACCEPTING_WORK",
        ) from exc
    except ValueError as exc:
        raise BusinessError(str(exc), status_code=400, code="INVALID_VIDEO_ID") from exc
    if result["reason"] == "queue_unavailable":
        raise BusinessError("下载队列未能启动。", status_code=503, code="QUEUE_UNAVAILABLE")
    return ApiResponse[DownloadImmediateResult](
        data=DownloadImmediateResult.model_validate(result),
        message="已加入下载队列" if result["queued"] else "任务未重复加入",
    )


@router.get("/tasks/{task_id}", response_model=ApiResponse[DownloadTaskItem])
def get_download_task(
    task_id: str,
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadTaskItem]:
    task = service.get_task(task_id)
    if task is None:
        raise BusinessError("任务不存在。", status_code=404, code="TASK_NOT_FOUND")
    return ApiResponse[DownloadTaskItem](
        data=DownloadTaskItem.model_validate(task),
        message="任务详情",
    )


@router.post("/tasks/{task_id}/retry", response_model=ApiResponse[DownloadTaskItem])
def retry_download_task(
    task_id: str,
    runtime: RuntimeServices = Depends(get_runtime_services),
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadTaskItem]:
    try:
        with runtime.work_lease():
            task = service.retry_task(task_id)
    except RuntimeWorkUnavailableError as exc:
        raise BusinessError(
            str(exc),
            status_code=503,
            code="RUNTIME_NOT_ACCEPTING_WORK",
        ) from exc
    except ValueError as exc:
        raise BusinessError(str(exc), status_code=409, code="TASK_NOT_RETRYABLE") from exc
    if task is None:
        raise BusinessError("任务不存在。", status_code=404, code="TASK_NOT_FOUND")
    return ApiResponse[DownloadTaskItem](
        data=DownloadTaskItem.model_validate(task),
        message="任务已重新加入待下载",
    )


@router.delete("/tasks/{task_id}", response_model=ApiResponse[DownloadDeleteData])
def delete_download_task(
    task_id: str,
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadDeleteData]:
    result = service.remove_task(task_id)
    if result["reason"] == "not_found":
        raise BusinessError("任务不存在。", status_code=404, code="TASK_NOT_FOUND")
    if result["reason"] == "running":
        raise BusinessError("运行中的任务不能删除。", status_code=409, code="TASK_RUNNING")
    return ApiResponse[DownloadDeleteData](
        data=DownloadDeleteData.model_validate(result),
        message="任务已移除",
    )


@router.post("/tasks/clear-completed", response_model=ApiResponse[DownloadClearData])
def clear_completed_downloads(
    service: DownloadQueueService = Depends(get_download_service),
) -> ApiResponse[DownloadClearData]:
    removed = service.clear_completed()
    return ApiResponse[DownloadClearData](
        data=DownloadClearData(removed=removed),
        message="已完成任务已清理",
    )
