from typing import Any

from pydantic import BaseModel, Field


class CaptureStartRequest(BaseModel):
    target_url: str = Field(min_length=1)
    mode: str = Field(min_length=1)


class CaptureStartData(BaseModel):
    task_id: str
    status: str = "running"


class CaptureStatusData(BaseModel):
    task_id: str | None = None
    status: str = "idle"
    message: str = ""
    progress: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    stopping: bool = False
    stop_requested_at: float | None = None


class CaptureResultItem(BaseModel):
    video_id: str
    title: str


class CaptureResultsData(BaseModel):
    task_id: str | None = None
    items: list[CaptureResultItem] = Field(default_factory=list)
    total: int = 0


class CaptureStopData(BaseModel):
    stopping: bool


class CaptureClearData(BaseModel):
    cleared: bool
