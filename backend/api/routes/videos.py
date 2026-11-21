from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.services.video_query_service import VideoQueryService

from ..dependencies import get_video_query_service
from ..schemas import ApiResponse, VideoPageData, VideoSummaryData


router = APIRouter(prefix="/api/videos", tags=["videos"])


def _video_page_response(data: dict) -> ApiResponse[VideoPageData]:
    return ApiResponse[VideoPageData](
        data=VideoPageData.model_validate(data),
        message="视频库查询成功",
    )


@router.get("", response_model=ApiResponse[VideoPageData])
def list_videos(
    keyword: str = Query("", max_length=200),
    status_filter: Literal["all", "pending", "downloaded", "queued", "failed"] = Query(
        "all",
        alias="status",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort_by: Literal["library", "video_id", "title"] = Query("library"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
    service: VideoQueryService = Depends(get_video_query_service),
) -> ApiResponse[VideoPageData]:
    return _video_page_response(
        service.list_videos(
            keyword=keyword,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    )


@router.get("/summary", response_model=ApiResponse[VideoSummaryData])
def video_summary(
    service: VideoQueryService = Depends(get_video_query_service),
) -> ApiResponse[VideoSummaryData]:
    return ApiResponse[VideoSummaryData](
        data=VideoSummaryData.model_validate(service.summary()),
        message="视频库概览",
    )
