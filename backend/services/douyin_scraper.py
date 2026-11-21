import logging
import os
import time

import psutil
from scrapling.fetchers import StealthySession

from backend.repositories import get_video_library_df, merge_video_library
from backend.services.douyin_parser import (
    extract_cards_from_html,
    extract_ids_from_text,
    extract_router_data,
)
from backend.services.task_manager import browser_profile_guard
from config import (
    CAPTURE_STOP_CHECK_SECONDS,
    CAPTURE_DOM_SCAN_SECONDS,
    PAGE_LOAD_TIMEOUT_MS,
    PAGE_LOAD_WAIT_MS,
    SCROLL_COUNT,
    SCROLL_DISTANCE,
    SCROLL_SETTLE_MS,
    SCROLL_WAIT_MS,
    USER_DATA_DIR,
)


logger = logging.getLogger(__name__)
MAX_NETWORK_BODY_BYTES = 8 * 1024 * 1024


def _report_progress(progress_callback, message):
    if progress_callback is None:
        return
    try:
        progress_callback(message)
    except Exception:
        pass


def _response_matches_mode(mode: str, url: str) -> bool:
    normalized_url = str(url or "").lower()
    if mode == "recommend":
        return "feed" in normalized_url
    if mode == "user":
        return "aweme/post" in normalized_url
    if mode == "search":
        return "search" in normalized_url
    if mode == "jingxuan":
        return any(
            token in normalized_url
            for token in ("feed", "discover", "jingxuan", "aweme")
        )
    return True


def fetch_ids_task_generator(
    target_url,
    mode,
    stop_signal_checker,
    progress_callback=None,
):
    """Open a real browser, wait for the user to stop, then parse loaded data."""
    video_data, found_ids = {}, set()
    page_action_error = None
    network_scanned = False

    def scan_dom(page):
        try:
            html_text = page.content()
            if not html_text:
                return
            extract_router_data(html_text, found_ids, video_data)
            extract_cards_from_html(html_text, found_ids, mode, video_data)
        except Exception:
            logger.debug("扫描页面 DOM 失败。", exc_info=True)

    def extract_network_responses(session):
        nonlocal network_scanned
        if network_scanned:
            return
        network_scanned = True

        network = getattr(session, "network", None)
        if network is None:
            logger.warning("Scrapling Session 不提供网络响应记录。")
            return

        for index, response in enumerate(
            network.search(limit=None, include_static=False),
            start=1,
        ):
            if not _response_matches_mode(mode, response.url):
                continue

            body = getattr(response, "body", b"")
            if body and len(body) <= MAX_NETWORK_BODY_BYTES:
                extract_ids_from_text(body, found_ids, video_data)
            if index % 100 == 0:
                _report_progress(
                    progress_callback,
                    f"已检查 {index} 条网络响应...",
                )

    def capture_page(page):
        nonlocal page_action_error
        try:
            for _ in range(SCROLL_COUNT):
                if stop_signal_checker():
                    break
                page.mouse.wheel(0, SCROLL_DISTANCE)
                page.wait_for_timeout(SCROLL_WAIT_MS)
                scan_dom(page)
            page.wait_for_timeout(SCROLL_SETTLE_MS)
            scan_dom(page)

            _report_progress(
                progress_callback,
                "页面已加载。可继续手动滚动，完成后点击停止抓取。",
            )
            last_dom_scan = time.monotonic()
            while not stop_signal_checker():
                page.wait_for_timeout(int(CAPTURE_STOP_CHECK_SECONDS * 1000))
                now = time.monotonic()
                if now - last_dom_scan >= CAPTURE_DOM_SCAN_SECONDS:
                    scan_dom(page)
                    last_dom_scan = now

            _report_progress(
                progress_callback,
                "已收到停止信号，正在读取页面 DOM...",
            )
            scan_dom(page)
            _report_progress(progress_callback, "正在提取网络响应数据...")
            extract_network_responses(session)
        except Exception as exc:
            page_action_error = exc
            logger.exception("抓取页面处理失败。")

    try:
        _report_progress(progress_callback, "正在启动浏览器...")
        with browser_profile_guard("douyin-capture"), StealthySession(
            record_requests=True,
            headless=False,
            user_data_dir=USER_DATA_DIR,
            network_idle=False,
        ) as session:
            session.fetch(
                target_url,
                page_action=capture_page,
                wait=PAGE_LOAD_WAIT_MS,
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )

        if page_action_error is not None:
            raise page_action_error

        ordered_ids = sorted(found_ids, key=int)
        existing_titles = {}
        try:
            library = get_video_library_df()
            if not library.empty:
                existing_titles = {
                    str(row["视频ID"]).strip(): str(row["标题"]).strip()
                    for _, row in library.iterrows()
                    if str(row["视频ID"]).strip()
                }
        except Exception:
            logger.debug("读取现有视频标题失败。", exc_info=True)

        valid_rows = []
        for video_id in ordered_ids:
            title = (
                video_data.get(video_id, "").strip()
                or existing_titles.get(video_id, "").strip()
                or "无标题"
            )
            valid_rows.append({"视频ID": video_id, "标题": title})

        if valid_rows:
            _report_progress(progress_callback, "正在保存视频库...")
            merge_video_library(valid_rows)

        _report_progress(progress_callback, f"提取完成，共获得 {len(valid_rows)} 个视频。")
        yield valid_rows
    except Exception as exc:
        raise RuntimeError(f"浏览器运行失败：{exc}") from exc


def _normalized_profile_path(value):
    return os.path.normcase(os.path.normpath(os.path.abspath(str(value))))


def _process_uses_profile(cmdline, expected_profile):
    args = list(cmdline or [])
    for index, argument in enumerate(args):
        value = None
        if argument.startswith("--user-data-dir="):
            value = argument.split("=", 1)[1]
        elif argument == "--user-data-dir" and index + 1 < len(args):
            value = args[index + 1]
        if (
            value is not None
            and _normalized_profile_path(value.strip().strip('"')) == expected_profile
        ):
            return True
    return False


def kill_scraper_browsers_only(profile_path=None, timeout=8):
    """Kill only Chromium processes using the exact configured scraper profile."""
    expected_profile = _normalized_profile_path(profile_path or USER_DATA_DIR)
    killed_pids = set()
    to_kill = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in ("chrome.exe", "chromium.exe", "chrome", "chromium"):
                continue
            cmdline = proc.info.get("cmdline") or []
            if _process_uses_profile(cmdline, expected_profile):
                to_kill.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for proc in to_kill:
        try:
            children = proc.children(recursive=True)
            proc.kill()
            killed_pids.add(proc.pid)
            for child in children:
                try:
                    child.kill()
                    killed_pids.add(child.pid)
                except Exception:
                    pass
        except Exception:
            pass
    deadline = time.time() + timeout
    for pid in killed_pids:
        try:
            p = psutil.Process(pid)
            remaining = max(0, deadline - time.time())
            p.wait(timeout=remaining)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
    return len(killed_pids)
