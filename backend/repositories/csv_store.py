import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import pandas as pd


class CSVReadError(RuntimeError):
    """Raised when a strict CSV read cannot preserve existing data safely."""


_locks_guard = threading.Lock()
_path_locks = {}


def _normalized_path(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _lock_for(path):
    key = _normalized_path(path)
    with _locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _path_locks[key] = lock
        return lock


@contextmanager
def csv_write_lock(path):
    """Serialize read-modify-write operations for one CSV path."""
    with _lock_for(path):
        yield


def read_csv_safe(path, columns, strict=False):
    """Read a UTF-8 BOM CSV while preserving string IDs and existing columns."""
    path = os.fspath(path)
    columns = list(columns)
    if not os.path.exists(path):
        return pd.DataFrame(columns=columns)

    try:
        frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)
    except (pd.errors.ParserError, OSError, UnicodeError) as exc:
        if strict:
            raise CSVReadError(f"读取 CSV 失败，已中止写入：{path}") from exc
        return pd.DataFrame(columns=columns)

    for column in columns:
        if column not in frame.columns:
            frame[column] = ""

    if "视频ID" in frame.columns:
        frame["视频ID"] = frame["视频ID"].fillna("").astype(str).str.strip()
    return frame


def write_csv_atomic(frame, path):
    """Write one CSV through a unique temporary file and atomic replace."""
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    temp_name = (
        f"{Path(path).name}.{os.getpid()}."
        f"{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    temp_path = os.path.join(directory, temp_name)

    with csv_write_lock(path):
        try:
            frame.to_csv(temp_path, index=False, encoding="utf-8-sig")
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
