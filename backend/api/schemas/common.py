from typing import Generic, Literal, TypeVar

from pydantic import BaseModel


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    message: str = ""


class HealthStatus(BaseModel):
    status: Literal["running"] = "running"
    product_id: str = ""
    version: str = ""
    channel: str = ""
    tag: str = ""
    commit: str = ""
    build_time: str = ""
    updater_version: str = ""

