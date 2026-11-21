from pydantic import BaseModel


class VideoItem(BaseModel):
    video_id: str
    title: str
    status: str
    status_label: str
    action_label: str
    task_id: str | None = None


class VideoPageData(BaseModel):
    items: list[VideoItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class VideoSummaryData(BaseModel):
    total: int
    downloaded_total: int
    pending_total: int
    waiting_tasks: int
    running_tasks: int
    failed_tasks: int
