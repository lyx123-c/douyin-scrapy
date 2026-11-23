from fastapi import APIRouter, Depends

from config import (
    BUILD_TIME,
    PRODUCT_ID,
    PRODUCT_VERSION,
    RELEASE_COMMIT,
    RELEASE_TAG,
    UPDATE_CHANNEL,
    UPDATER_VERSION,
)

from backend.services.task_manager import TaskManager

from ..dependencies import get_task_manager
from ..schemas import ApiResponse, HealthStatus


router = APIRouter(prefix="/api", tags=["system"])


@router.get(
    "/health",
    response_model=ApiResponse[HealthStatus],
    summary="API 健康检查",
)
def health(
    task_manager: TaskManager = Depends(get_task_manager),
) -> ApiResponse[HealthStatus]:
    # The dependency only reads app.state; it does not touch CSV or JSON files.
    _ = task_manager
    return ApiResponse[HealthStatus](
        data=HealthStatus(
            status="running",
            product_id=PRODUCT_ID,
            version=PRODUCT_VERSION,
            channel=UPDATE_CHANNEL,
            tag=RELEASE_TAG,
            commit=RELEASE_COMMIT,
            build_time=BUILD_TIME,
            updater_version=UPDATER_VERSION,
        ),
        message="API 正常运行",
    )

