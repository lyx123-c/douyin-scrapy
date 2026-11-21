from fastapi import APIRouter, Depends, Query

from backend.services.video_query_service import VideoQueryService

from ..dependencies import get_video_query_service
from ..schemas import AnalyticsData, ApiResponse


router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("", response_model=ApiResponse[AnalyticsData])
def analytics(
    keyword: str = Query("", max_length=200),
    service: VideoQueryService = Depends(get_video_query_service),
) -> ApiResponse[AnalyticsData]:
    return ApiResponse[AnalyticsData](
        data=AnalyticsData.model_validate(service.analytics(keyword=keyword)),
        message="统计数据查询成功",
    )
