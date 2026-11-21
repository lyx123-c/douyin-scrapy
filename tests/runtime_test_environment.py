import atexit
import os
import shutil
import tempfile
from pathlib import Path


_created_root: Path | None = None


def _cleanup_created_root() -> None:
    if _created_root is not None:
        shutil.rmtree(_created_root, ignore_errors=True)


def activate() -> Path:
    global _created_root

    configured_root = os.environ.get("DOUYIN_APP_USER_DATA_DIR", "").strip()
    if configured_root:
        return Path(configured_root).expanduser()

    root = Path(tempfile.mkdtemp(prefix="douyin_backend_tests_")).resolve()
    os.environ["DOUYIN_APP_USER_DATA_DIR"] = str(root)
    os.environ.setdefault("DOUYIN_SAVE_DIR", str(root / "videos"))
    _created_root = root
    atexit.register(_cleanup_created_root)
    return root


TEST_RUNTIME_ROOT = activate()
