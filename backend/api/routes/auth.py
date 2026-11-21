from fastapi import APIRouter, Depends

from backend.services.douyin_auth import login_douyin, login_profile_ready
from backend.services.task_manager import TaskManager

from ..dependencies import get_task_manager
from ..errors import BusinessError
from ..schemas import ApiResponse, AuthLoginData, AuthStatusData


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status", response_model=ApiResponse[AuthStatusData])
def auth_status() -> ApiResponse[AuthStatusData]:
    return ApiResponse[AuthStatusData](
        data=AuthStatusData(login_ready=login_profile_ready()),
        message="登录状态",
    )


@router.post("/login", response_model=ApiResponse[AuthLoginData])
def auth_login(
    task_manager: TaskManager = Depends(get_task_manager),
) -> ApiResponse[AuthLoginData]:
    try:
        login_douyin(task_manager)
    except Exception as exc:
        raise BusinessError(f"登录失败：{exc}", status_code=409, code="LOGIN_FAILED") from exc
    return ApiResponse[AuthLoginData](
        data=AuthLoginData(profile_saved=True),
        message="登录流程已结束",
    )
