from fastapi import APIRouter, Depends, HTTPException

from backend.services.douyin_auth import (
    LOGIN_STATE_READY,
    LOGIN_STATE_UNKNOWN,
    login_profile_state,
)
from backend.services.capture_service import CaptureService

from ..dependencies import get_capture_service
from ..errors import BusinessError
from ..schemas import (
    ApiResponse,
    CaptureClearData,
    CaptureResultsData,
    CaptureStartData,
    CaptureStartRequest,
    CaptureStatusData,
    CaptureStopData,
)


router = APIRouter(prefix="/api/capture", tags=["capture"])


@router.post("/start", response_model=ApiResponse[CaptureStartData])
def start_capture(
    payload: CaptureStartRequest,
    service: CaptureService = Depends(get_capture_service),
) -> ApiResponse[CaptureStartData]:
    login_state = login_profile_state()
    if login_state == LOGIN_STATE_UNKNOWN:
        raise BusinessError(
            "无法确认抖音登录状态，暂时不能开始抓取。",
            status_code=503,
            code="LOGIN_STATUS_UNKNOWN",
        )
    if login_state != LOGIN_STATE_READY:
        raise HTTPException(
            status_code=401,
            detail="请先完成抖音登录后再开始抓取。",
        )

    try:
        task_id = service.start_capture(payload.target_url, payload.mode)
    except ValueError as exc:
        raise BusinessError(str(exc), status_code=400, code="INVALID_CAPTURE_REQUEST") from exc
    except Exception as exc:
        raise BusinessError(
            f"抓取启动失败：{exc}",
            status_code=503,
            code="CAPTURE_START_FAILED",
        ) from exc

    if task_id is None:
        raise BusinessError(
            "已有抓取或下载任务正在运行。",
            status_code=409,
            code="CAPTURE_BUSY",
        )
    return ApiResponse[CaptureStartData](
        data=CaptureStartData(task_id=task_id, status="running"),
        message="抓取任务已启动",
    )


@router.post("/stop", response_model=ApiResponse[CaptureStopData])
def stop_capture(
    service: CaptureService = Depends(get_capture_service),
) -> ApiResponse[CaptureStopData]:
    if not service.request_stop():
        raise BusinessError(
            "当前没有可停止的抓取任务，或任务已经在停止中。",
            status_code=409,
            code="CAPTURE_NOT_RUNNING",
        )
    return ApiResponse[CaptureStopData](
        data=CaptureStopData(stopping=True),
        message="正在停止抓取并提取已加载数据",
    )


@router.get("/status", response_model=ApiResponse[CaptureStatusData])
def capture_status(
    service: CaptureService = Depends(get_capture_service),
) -> ApiResponse[CaptureStatusData]:
    return ApiResponse[CaptureStatusData](
        data=CaptureStatusData.model_validate(service.get_public_status()),
        message="抓取状态",
    )


@router.get("/results", response_model=ApiResponse[CaptureResultsData])
def capture_results(
    service: CaptureService = Depends(get_capture_service),
) -> ApiResponse[CaptureResultsData]:
    return ApiResponse[CaptureResultsData](
        data=CaptureResultsData.model_validate(service.get_results()),
        message="抓取结果",
    )


@router.post("/clear", response_model=ApiResponse[CaptureClearData])
def clear_capture(
    service: CaptureService = Depends(get_capture_service),
) -> ApiResponse[CaptureClearData]:
    service.clear_status()
    return ApiResponse[CaptureClearData](
        data=CaptureClearData(cleared=True),
        message="抓取状态已清理",
    )
