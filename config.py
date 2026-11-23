import os
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PRODUCT_ID = os.environ.get("DOUYIN_PRODUCT_ID", "").strip()
PRODUCT_VERSION = os.environ.get("DOUYIN_PRODUCT_VERSION", "").strip()
UPDATE_CHANNEL = os.environ.get("DOUYIN_UPDATE_CHANNEL", "").strip()
RELEASE_TAG = os.environ.get("DOUYIN_RELEASE_TAG", "").strip()
RELEASE_COMMIT = os.environ.get("DOUYIN_RELEASE_COMMIT", "").strip()
BUILD_TIME = os.environ.get("DOUYIN_BUILD_TIME", "").strip()
UPDATER_VERSION = os.environ.get("DOUYIN_UPDATER_VERSION", "").strip()

# Development defaults intentionally retain the current local data locations.
# Production/Electron must provide DOUYIN_APP_USER_DATA_DIR.
LEGACY_VIDEO_DIR = Path(r"D:\下载\PythonProject4\爬虫\scrapling_c\视频")
LEGACY_USER_DATA_DIR = Path(r"D:\下载\PythonProject4\爬虫\douyin_user_data")
LEGACY_FFMPEG_PATH = Path(r"E:\360Downloads\ffmpeg.exe")


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _env_path(name: str, default: Path) -> str:
    value = _env_value(name)
    path = Path(value).expanduser() if value else default
    return str(path)


_app_user_data_env = _env_value("DOUYIN_APP_USER_DATA_DIR")
USING_EXTERNAL_APP_DATA = bool(_app_user_data_env)
APP_USER_DATA_PATH = (
    Path(_app_user_data_env).expanduser()
    if _app_user_data_env
    else LEGACY_VIDEO_DIR
)

# APP_USER_DATA_DIR is the application data root. USER_DATA_DIR is specifically
# the persistent Chromium Profile and must remain independently configurable.
VIDEO_DATA_PATH = Path(
    _env_path(
        "DOUYIN_VIDEO_DATA_DIR",
        (
            APP_USER_DATA_PATH / "data"
            if USING_EXTERNAL_APP_DATA
            else LEGACY_VIDEO_DIR
        ),
    )
).expanduser()
TASK_DATA_PATH = Path(
    _env_path(
        "DOUYIN_TASK_DATA_DIR",
        (
            APP_USER_DATA_PATH / "tasks"
            if USING_EXTERNAL_APP_DATA
            else PROJECT_ROOT / "data"
        ),
    )
).expanduser()
USER_DATA_PATH = Path(
    _env_path(
        "DOUYIN_BROWSER_PROFILE_DIR",
        (
            APP_USER_DATA_PATH / "browser-profile"
            if USING_EXTERNAL_APP_DATA
            else LEGACY_USER_DATA_DIR
        ),
    )
).expanduser()
SAVE_PATH = Path(
    _env_path(
        "DOUYIN_SAVE_DIR",
        (
            Path.home() / "Videos" / "抖音视频工作台"
            if USING_EXTERNAL_APP_DATA
            else LEGACY_VIDEO_DIR / "mmp"
        ),
    )
).expanduser()
ANALYTICS_CSV_PATH = Path(
    _env_path(
        "DOUYIN_ANALYTICS_CSV",
        VIDEO_DATA_PATH / "video_analytics.csv",
    )
).expanduser()
VIDEO_TITLE_CSV_PATH = Path(
    _env_path(
        "DOUYIN_VIDEOS_CSV",
        VIDEO_DATA_PATH / "douyin_videos.csv",
    )
).expanduser()
DOWNLOAD_QUEUE_PATH = Path(
    _env_path(
        "DOUYIN_DOWNLOAD_QUEUE_JSON",
        TASK_DATA_PATH / "download_queue.json",
    )
).expanduser()
LOG_PATH = Path(
    _env_path(
        "DOUYIN_LOG_DIR",
        (
            APP_USER_DATA_PATH / "logs"
            if USING_EXTERNAL_APP_DATA
            else PROJECT_ROOT / "logs"
        ),
    )
).expanduser()
FFMPEG_FILE_PATH = Path(
    _env_path(
        "FFMPEG_PATH",
        PROJECT_ROOT / "ffmpeg.exe" if USING_EXTERNAL_APP_DATA else LEGACY_FFMPEG_PATH,
    )
).expanduser()

APP_USER_DATA_DIR = str(APP_USER_DATA_PATH)
VIDEO_DATA_DIR = str(VIDEO_DATA_PATH)
TASK_DATA_DIR = str(TASK_DATA_PATH)
USER_DATA_DIR = str(USER_DATA_PATH)
SAVE_DIR = str(SAVE_PATH)
CSV_FILE = str(ANALYTICS_CSV_PATH)
VIDEO_TITLE_CSV = str(VIDEO_TITLE_CSV_PATH)
DOWNLOAD_QUEUE_JSON = str(DOWNLOAD_QUEUE_PATH)
LOG_DIR = str(LOG_PATH)
FFMPEG_PATH = str(FFMPEG_FILE_PATH)

RUNTIME_PATHS = {
    "app_user_data": APP_USER_DATA_PATH,
    "video_data": VIDEO_DATA_PATH,
    "task_data": TASK_DATA_PATH,
    "browser_profile": USER_DATA_PATH,
    "video_save": SAVE_PATH,
    "analytics_csv": ANALYTICS_CSV_PATH,
    "video_library_csv": VIDEO_TITLE_CSV_PATH,
    "download_queue_json": DOWNLOAD_QUEUE_PATH,
    "logs": LOG_PATH,
    "ffmpeg": FFMPEG_FILE_PATH,
}


def ensure_runtime_directories() -> dict[str, Path]:
    """Create configured runtime directories without moving or deleting user data."""
    directories = [
        APP_USER_DATA_PATH,
        VIDEO_DATA_PATH,
        TASK_DATA_PATH,
        USER_DATA_PATH,
        SAVE_PATH,
        LOG_PATH,
        ANALYTICS_CSV_PATH.parent,
        VIDEO_TITLE_CSV_PATH.parent,
        DOWNLOAD_QUEUE_PATH.parent,
    ]
    for directory in dict.fromkeys(directories):
        directory.mkdir(parents=True, exist_ok=True)
    return RUNTIME_PATHS


REQUEST_HEADERS = {
    "Referer": "https://www.douyin.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

AWEME_ID_RE = re.compile(r"(?<!\d)(7\d{18})(?!\d)")
VIDEO_PATH_RE = re.compile(r"/video/(7\d{18})(?!\d)")
SEARCH_ITEM_RE = re.compile(r"waterfall_item_(7\d{18})")
ID_KEYS = {"awemeid", "awemeidstr", "groupid", "groupidstr", "itemid", "itemidstr"}

SCROLL_COUNT = 6
SCROLL_DISTANCE = 900
SCROLL_WAIT_MS = 600
SCROLL_SETTLE_MS = 400
PAGE_LOAD_WAIT_MS = 500
PAGE_LOAD_TIMEOUT_MS = 15000

CAPTURE_STOP_GRACE_SECONDS = 15
CAPTURE_STOP_CHECK_SECONDS = 0.2
CAPTURE_DOM_SCAN_SECONDS = 1.0
CAPTURE_UI_POLL_SECONDS = 0.25
DOWNLOAD_UI_POLL_SECONDS = 1.0
DOWNLOAD_RETRY_COUNT = 3
LOGIN_WAIT_SECONDS = max(
    5,
    int(os.environ.get("DOUYIN_LOGIN_WAIT_SECONDS", "120")),
)
DOUYIN_LOGIN_URL = "https://www.douyin.com/"
BACKEND_API_BASE_URL = os.environ.get(
    "DOUYIN_BACKEND_API_BASE_URL",
    "http://127.0.0.1:8000",
).rstrip("/")
BACKEND_API_CONNECT_TIMEOUT = 3.0
BACKEND_API_READ_TIMEOUT = 15.0
BACKEND_API_LOGIN_TIMEOUT = LOGIN_WAIT_SECONDS + 30
BACKEND_RUNTIME_MUTEX_NAME = os.environ.get(
    "DOUYIN_RUNTIME_MUTEX_NAME",
    r"Local\DouyinVideoWorkbenchBackendRuntime",
)

VIDEO_COLUMNS = ["视频ID", "标题"]
ANALYTICS_COLUMNS = [
    "视频ID",
    "作者昵称",
    "视频标题",
    "点赞量",
    "评论量",
    "收藏量",
    "转发量",
]
TASK_LABELS = {
    "interrupted": "已中断",
    "waiting": "待下载",
    "running": "下载中",
    "success": "已完成",
    "failed": "失败",
    "skipped": "已存在",
}
