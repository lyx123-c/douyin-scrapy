from pydantic import BaseModel


class AnalyticsItem(BaseModel):
    video_id: str
    author_name: str
    video_title: str
    digg_count: str
    comment_count: str
    collect_count: str
    share_count: str


class AnalyticsData(BaseModel):
    items: list[AnalyticsItem]
    total: int
