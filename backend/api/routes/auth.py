from fastapi import APIRouter, Depends

from backend.services.douyin_auth import login_douyin, login_profile_ready
from backend.services.runtime import (
    RuntimeServices,
    RuntimeWorkUnavailableError,
)

from ..dependencies import get_runtime_services
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
    runtime: RuntimeServices = Depends(get_runtime_services),
) -> ApiResponse[AuthLoginData]:
    try:
        runtime.ensure_accepting_work()
        login_douyin(
            runtime.task_manager,
            work_lease=runtime.work_lease,
        )
    except RuntimeWorkUnavailableError as exc:
        raise BusinessError(
            str(exc),
            status_code=503,
            code="RUNTIME_NOT_ACCEPTING_WORK",
        ) from exc
    except Exception as exc:
        raise BusinessError(f"登录失败：{exc}", status_code=409, code="LOGIN_FAILED") from exc
    return ApiResponse[AuthLoginData](
        data=AuthLoginData(profile_saved=True),
        message="登录流程已结束",
    )
