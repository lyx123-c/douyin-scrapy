import os
from pathlib import Path

from config import SAVE_DIR


def open_save_directory() -> str:
    save_path = Path(SAVE_DIR).expanduser().resolve()
    save_path.mkdir(parents=True, exist_ok=True)
    if not save_path.is_dir():
        raise NotADirectoryError(f"视频保存路径不是目录：{save_path}")
    if not hasattr(os, "startfile"):
        raise OSError("当前系统不支持 os.startfile。")

    os.startfile(str(save_path))
    return str(save_path)

