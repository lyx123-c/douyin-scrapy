from .analytics import router as analytics_router
from .auth import router as auth_router
from .capture import router as capture_router
from .downloads import router as downloads_router
from .health import router as health_router
from .videos import router as videos_router
from .system import router as system_router

__all__ = [
    "analytics_router",
    "auth_router",
    "capture_router",
    "downloads_router",
    "health_router",
    "system_router",
    "videos_router",
]

