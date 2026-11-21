from typing import Any

from pydantic import BaseModel, Field


class DownloadTaskCreate(BaseModel):
    video_id: str
    title: str = "无标题"


class DownloadBatchRequest(BaseModel):
    items: list[DownloadTaskCreate] = Field(default_factory=list)


class ImmediateDownloadRequest(BaseModel):
    video_id: str
    title: str = "无标题"
    force: bool = False


class DownloadTaskItem(BaseModel):
    task_id: str
    task_type: str = "download"
    video_id: str | None = None
    title: str | None = None
    status: str
    progress: float = 0.0
    message: str = ""
    retry_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    error: str | None = None
    progress_details: dict[str, Any] | None = None


class DownloadQueueState(BaseModel):
    running: bool
    thread_alive: bool
    stopping: bool
    forced_task_count: int = 0
    counts: dict[str, int] = Field(default_factory=dict)


class DownloadSnapshotData(BaseModel):
    items: list[DownloadTaskItem] = Field(default_factory=list)
    total: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    queue: DownloadQueueState


class DownloadBatchResult(BaseModel):
    created: list[DownloadTaskItem] = Field(default_factory=list)
    existing: list[DownloadTaskItem] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    created_count: int = 0


class DownloadImmediateResult(BaseModel):
    queued: bool
    reason: str
    task: DownloadTaskItem


class DownloadStartData(BaseModel):
    started: bool
    running: bool
    queue: DownloadQueueState


class DownloadStopData(BaseModel):
    stopping: bool


class DownloadDeleteData(BaseModel):
    removed: bool
    reason: str


class DownloadClearData(BaseModel):
    removed: int
