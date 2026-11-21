from fastapi import APIRouter

from backend.services.system_service import open_save_directory

from ..errors import BusinessError
from ..schemas import ApiResponse, OpenSaveDirectoryData


router = APIRouter(prefix="/api/system", tags=["system"])


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
