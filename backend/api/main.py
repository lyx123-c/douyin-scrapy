import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import PRODUCT_VERSION, ensure_runtime_directories
from backend.services.runtime import DEFAULT_QUEUE_STORE, RuntimeServices

from .errors import register_exception_handlers
from .ownership import acquire_backend_runtime_ownership
from .routes import analytics_router, auth_router, capture_router, downloads_router, health_router, system_router, videos_router


logger = logging.getLogger(__name__)


def _clear_runtime_app_state(app: FastAPI) -> None:
    for attribute in ("runtime", "task_manager"):
        try:
            setattr(app.state, attribute, None)
        except BaseException:
            logger.exception(
                "Failed to clear FastAPI app state: %s",
                attribute,
            )


def _shutdown_runtime(runtime, *, timeout: float = 30.0) -> bool:
    try:
        stopped = bool(runtime.shutdown(timeout=timeout))
    except BaseException:
        logger.exception("FastAPI RuntimeServices shutdown raised an exception.")
        return False

    if not stopped:
        logger.warning(
            "FastAPI RuntimeServices shutdown did not complete cleanly."
        )
    return stopped

@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = None
    _clear_runtime_app_state(app)

    with acquire_backend_runtime_ownership():
        try:
            ensure_runtime_directories()
            runtime = RuntimeServices(store_path=DEFAULT_QUEUE_STORE)
            app.state.runtime = runtime
            app.state.task_manager = runtime.task_manager
            logger.info(
                "FastAPI RuntimeServices initialized: %s",
                DEFAULT_QUEUE_STORE,
            )
        except BaseException:
            logger.exception("FastAPI startup failed; cleaning partial Runtime state.")
            if runtime is not None:
                _shutdown_runtime(runtime)
            _clear_runtime_app_state(app)
            raise

        try:
            yield
        except BaseException:
            logger.exception("FastAPI lifespan body failed; cleaning Runtime state.")
            _shutdown_runtime(runtime)
            _clear_runtime_app_state(app)
            raise
        else:
            stopped = _shutdown_runtime(runtime)
            _clear_runtime_app_state(app)
            if stopped:
                logger.info("FastAPI RuntimeServices shutdown completed.")
            else:
                logger.warning(
                    "FastAPI RuntimeServices shutdown completed with failures; "
                    "app state references were cleared."
                )


app = FastAPI(
    title="抖音视频工作台 API",
    description="抖音视频工作台本地 FastAPI 服务。第一版必须单进程运行。",
    version=PRODUCT_VERSION or "0.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(system_router)
app.include_router(videos_router)
app.include_router(analytics_router)
app.include_router(capture_router)
app.include_router(downloads_router)

