from fastapi import APIRouter, BackgroundTasks, Depends, Request

from config import (
    APP_USER_DATA_DIR,
    FFMPEG_PATH,
    LOG_DIR,
    SAVE_DIR,
    TASK_DATA_DIR,
    USER_DATA_DIR,
    USING_EXTERNAL_APP_DATA,
    VIDEO_DATA_DIR,
)
from backend.services.system_service import open_save_directory
from ..dependencies import get_runtime_services

from ..errors import BusinessError
from ..schemas import (
    ApiResponse,
    OpenSaveDirectoryData,
    PrepareUpdateShutdownData,
    StorageRuntimePathsData,
)


router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/storage", response_model=ApiResponse[StorageRuntimePathsData])
def storage_paths() -> ApiResponse[StorageRuntimePathsData]:
    return ApiResponse[StorageRuntimePathsData](
        data=StorageRuntimePathsData(
            app_user_data_dir=APP_USER_DATA_DIR,
            video_data_dir=VIDEO_DATA_DIR,
            task_data_dir=TASK_DATA_DIR,
            browser_profile_dir=USER_DATA_DIR,
            save_dir=SAVE_DIR,
            log_dir=LOG_DIR,
            ffmpeg_path=FFMPEG_PATH,
            using_external_app_data=USING_EXTERNAL_APP_DATA,
        ),
        message="存储路径配置",
    )

@router.post(
    "/prepare-update-shutdown",
    response_model=ApiResponse[PrepareUpdateShutdownData],
)
def prepare_update_shutdown(
    background_tasks: BackgroundTasks,
    request: Request,
    runtime=Depends(get_runtime_services),
) -> ApiResponse[PrepareUpdateShutdownData]:
    shutdown_callback = getattr(request.app.state, "request_process_shutdown", None)
    if not callable(shutdown_callback):
        raise BusinessError(
            "当前 Backend 不支持更新关闭握手。",
            status_code=503,
            code="UPDATE_SHUTDOWN_UNSUPPORTED",
        )

    try:
        stopped = bool(runtime.shutdown(timeout=30.0))
    except Exception as exc:
        raise BusinessError(
            f"Runtime 安全关闭失败：{exc}",
            status_code=503,
            code="UPDATE_SHUTDOWN_FAILED",
        ) from exc
    if not stopped:
        raise BusinessError(
            "Runtime 未能安全停止，已取消安装交接。",
            status_code=503,
            code="UPDATE_SHUTDOWN_FAILED",
        )

    background_tasks.add_task(shutdown_callback)
    return ApiResponse[PrepareUpdateShutdownData](
        data=PrepareUpdateShutdownData(
            accepted=True,
            runtime_stopped=True,
            shutdown_scheduled=True,
        ),
        message="Runtime 已安全停止，进程关闭已调度",
    )

@router.post("/open-save-directory", response_model=ApiResponse[OpenSaveDirectoryData])
def open_directory() -> ApiResponse[OpenSaveDirectoryData]:
    try:
        path = open_save_directory()
    except Exception as exc:
        raise BusinessError(f"无法打开目录：{exc}", status_code=500, code="OPEN_DIRECTORY_FAILED") from exc
    return ApiResponse[OpenSaveDirectoryData](
        data=OpenSaveDirectoryData(path=path),
        message="视频目录已打开",
    )
