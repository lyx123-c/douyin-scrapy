import logging
import os
import sys
import time
from pathlib import Path

import psutil
from scrapling.fetchers import StealthySession

from backend.services.task_manager import TaskManager, browser_profile_guard
from config import DOUYIN_LOGIN_URL, LOGIN_WAIT_SECONDS, USER_DATA_DIR


logger = logging.getLogger(__name__)
LOGIN_PROFILE_LOCK_TIMEOUT = 0.5
LOGIN_CLOSE_POLL_MS = 250
LOGIN_STATE_READY = "ready"
LOGIN_STATE_MISSING = "missing"
LOGIN_STATE_UNKNOWN = "unknown"


class LoginBrowserClosedError(RuntimeError):
    """Raised when the user closes the login browser before the wait ends."""


def login_profile_state() -> str:
    """Return the existing Profile-based readiness state without claiming auth validity."""
    try:
        profile_path = Path(USER_DATA_DIR).expanduser()
        if not profile_path.exists():
            return LOGIN_STATE_MISSING
        try:
            return (
                LOGIN_STATE_READY
                if any(True for _ in profile_path.iterdir())
                else LOGIN_STATE_MISSING
            )
        except OSError:
            logger.warning("读取登录 Profile 状态失败。", exc_info=True)
            return LOGIN_STATE_UNKNOWN
    except OSError:
        logger.warning("解析登录 Profile 路径失败。", exc_info=True)
        return LOGIN_STATE_UNKNOWN


def login_profile_ready() -> bool:
    return login_profile_state() == LOGIN_STATE_READY


def _normalized_path(value) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(value))))


def _process_uses_profile(cmdline, expected_profile: str) -> bool:
    args = list(cmdline or [])
    for index, argument in enumerate(args):
        value = None
        if argument.startswith("--user-data-dir="):
            value = argument.split("=", 1)[1]
        elif argument == "--user-data-dir" and index + 1 < len(args):
            value = args[index + 1]
        if value is None:
            continue
        if _normalized_path(value.strip().strip('"')) == expected_profile:
            return True
    return False


def cleanup_residual_login_processes(profile_path) -> int:
    """Kill only Chromium processes using this application's exact profile path."""
    expected_profile = _normalized_path(profile_path)
    targets = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (process.info.get("name") or "").lower()
            if name not in ("chrome.exe", "chromium.exe", "chrome", "chromium"):
                continue
            if _process_uses_profile(process.info.get("cmdline"), expected_profile):
                targets.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    killed = 0
    for process in targets:
        try:
            children = process.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []
        try:
            process.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for child in children:
            try:
                child.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    if killed:
        logger.warning("已清理本项目登录 Profile 的残留浏览器进程：%d 个", killed)
    return killed


def _page_is_closed(page) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return True


def _wait_for_login_window_close(page, wait_seconds: float) -> bool:
    closed = False

    def mark_closed(*_args):
        nonlocal closed
        closed = True

    try:
        page.on("close", mark_closed)
    except Exception:
        logger.debug("无法注册登录页面关闭监听。", exc_info=True)

    try:
        page.bring_to_front()
    except Exception:
        if _page_is_closed(page):
            return True
        raise

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() < deadline:
        if closed or _page_is_closed(page):
            return True
        try:
            page.wait_for_timeout(LOGIN_CLOSE_POLL_MS)
        except Exception:
            if closed or _page_is_closed(page):
                return True
            raise
    return closed or _page_is_closed(page)


def login_douyin(task_manager: TaskManager, *, work_lease) -> str:
    if task_manager.is_capture_running():
        raise RuntimeError("抓取任务正在使用浏览器 Profile，请先停止抓取。")
    if task_manager.is_download_running():
        raise RuntimeError("下载任务正在使用浏览器 Profile，请等待任务结束后重试。")

    profile_path = Path(USER_DATA_DIR).expanduser().resolve()
    browser_closed = False

    def wait_for_login(page):
        nonlocal browser_closed
        browser_closed = _wait_for_login_window_close(page, LOGIN_WAIT_SECONDS)

    with browser_profile_guard("login", timeout=LOGIN_PROFILE_LOCK_TIMEOUT):
        with work_lease():
            profile_path.mkdir(parents=True, exist_ok=True)
            cleanup_residual_login_processes(profile_path)
            session_context = StealthySession(
                headless=False,
                user_data_dir=str(profile_path),
                network_idle=False,
                retries=1,
            )
            session = session_context.__enter__()

        try:
            try:
                session.fetch(
                    DOUYIN_LOGIN_URL,
                    page_action=wait_for_login,
                    wait=0,
                )
            except Exception as exc:
                if browser_closed:
                    raise LoginBrowserClosedError(
                        "登录浏览器已关闭，登录流程已结束。"
                    ) from exc
                raise
        finally:
            session_context.__exit__(*sys.exc_info())

    if browser_closed:
        raise LoginBrowserClosedError("登录浏览器已关闭，登录流程已结束。")
    return str(profile_path)
