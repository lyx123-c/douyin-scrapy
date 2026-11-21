import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import ensure_runtime_directories
from backend.services.runtime import DEFAULT_QUEUE_STORE, RuntimeServices

from .errors import register_exception_handlers
from .ownership import acquire_backend_runtime_ownership
from .routes import analytics_router, auth_router, capture_router, downloads_router, health_router, system_router, videos_router


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with acquire_backend_runtime_ownership():
        ensure_runtime_directories()
        runtime = RuntimeServices(store_path=DEFAULT_QUEUE_STORE)
        app.state.runtime = runtime
        app.state.task_manager = runtime.task_manager
        logger.info("FastAPI RuntimeServices initialized: %s", DEFAULT_QUEUE_STORE)

        try:
            yield
        finally:
            stopped = runtime.shutdown(timeout=30)
            if not stopped:
                logger.warning("FastAPI RuntimeServices did not stop within the timeout.")
            app.state.runtime = None
            app.state.task_manager = None
            logger.info("FastAPI RuntimeServices shutdown completed.")


app = FastAPI(
    title="抖音视频工作台 API",
    description="抖音视频工作台本地 FastAPI 服务。第一版必须单进程运行。",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(system_router)
app.include_router(videos_router)
app.include_router(analytics_router)
app.include_router(capture_router)
app.include_router(downloads_router)

