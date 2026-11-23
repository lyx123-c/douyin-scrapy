import json
import logging
import os
import re
import sys
import time
from contextlib import nullcontext
from dataclasses import dataclass

import requests
from scrapling.fetchers import StealthySession

from backend.repositories import (
    get_title_from_library,
    is_already_downloaded,
    upsert_analytics_record,
)
from backend.services.video_merger import (
    FFmpegCancelledError,
    FFmpegExitUnconfirmedError,
    FFmpegProcessError,
    FFmpegRunResult,
    FFmpegTimeoutError,
    merge_video_and_audio,
    validate_merged_output,
)
from backend.services.task_manager import browser_profile_guard
from config import (
    AWEME_ID_RE,
    DOWNLOAD_RETRY_COUNT,
    REQUEST_HEADERS,
    SAVE_DIR,
    USER_DATA_DIR,
)


logger = logging.getLogger(__name__)
DEFAULT_MEDIA_READ_TIMEOUT = 60
MEDIA_CHUNK_SIZE = 256 * 1024


class DownloadCancelledError(RuntimeError):
    """Raised when a download task is cancelled via stop_event."""


@dataclass
class DownloadTaskResult:
    """Structured download task execution result compatible with TaskManager statuses."""

    status: str  # "success" | "failed" | "interrupted" | "skipped"
    message: str
    error: str | None = None
    cancelled: bool = False
    timed_out: bool = False
    exit_verified: bool = True
    output_file: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


def _is_cancelled(stop_event) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _interruptible_wait(seconds: float, stop_event) -> bool:
    """Wait up to `seconds`; return True if stop_event was triggered."""
    if stop_event is not None:
        return bool(stop_event.wait(timeout=max(0.0, float(seconds))))
    time.sleep(max(0.0, float(seconds)))
    return False


def _remove_temp_file_with_retry(
    path: str,
    *,
    retries: int = 3,
    delay: float = 0.15,
) -> None:
    """Delete a temp file with short retry backoff for transient Windows locks."""
    for attempt in range(max(1, int(retries))):
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt + 1 < retries:
                time.sleep(delay)
            else:
                logger.warning("清理临时文件仍被占用：%s", path, exc_info=True)
        except OSError:
            logger.warning("清理临时文件失败：%s", path, exc_info=True)
            return


def _report_progress(progress_callback, payload):
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except Exception:
        pass


def download_media(
    url,
    output_file,
    desc="下载",
    progress_callback=None,
    *,
    stop_event=None,
    read_timeout=DEFAULT_MEDIA_READ_TIMEOUT,
):
    part_file = f"{output_file}.part"
    downloaded = 0
    total_size = 0

    if _is_cancelled(stop_event):
        raise DownloadCancelledError(f"{desc}已取消。")

    _report_progress(
        progress_callback,
        {
            "stage": desc,
            "downloaded": 0,
            "total": 0,
            "progress": 0.0,
            "message": f"{desc} · 开始下载",
        },
    )

    try:
        with requests.get(
            url,
            headers={**REQUEST_HEADERS, "Accept-Encoding": "identity"},
            stream=True,
            timeout=(20, read_timeout),
        ) as response:
            if _is_cancelled(stop_event):
                raise DownloadCancelledError(f"{desc}已取消。")
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                raise RuntimeError("下载地址返回了 HTML，登录状态或媒体签名可能已失效。")

            total_size = int(response.headers.get("content-length", 0) or 0)
            with open(part_file, "wb") as file:
                for chunk in response.iter_content(chunk_size=MEDIA_CHUNK_SIZE):
                    if _is_cancelled(stop_event):
                        raise DownloadCancelledError(f"{desc}已取消。")
                    if not chunk:
                        continue
                    file.write(chunk)
                    downloaded += len(chunk)
                    progress = (
                        min(downloaded / total_size, 1.0)
                        if total_size > 0
                        else 0.5
                    )
                    if total_size > 0:
                        message = (
                            f"{desc} · {downloaded / 1024 / 1024:.1f}MB / "
                            f"{total_size / 1024 / 1024:.1f}MB"
                        )
                    else:
                        message = f"{desc} · 已下载 {downloaded / 1024 / 1024:.1f}MB"
                    _report_progress(
                        progress_callback,
                        {
                            "stage": desc,
                            "downloaded": downloaded,
                            "total": total_size,
                            "progress": progress,
                            "message": message,
                        },
                    )

        if _is_cancelled(stop_event):
            raise DownloadCancelledError(f"{desc}已取消。")

        if downloaded < 1024:
            raise RuntimeError(f"下载文件过小，仅 {downloaded} 字节。")

        os.replace(part_file, output_file)
        _report_progress(
            progress_callback,
            {
                "stage": desc,
                "downloaded": downloaded,
                "total": total_size or downloaded,
                "progress": 1.0,
                "message": f"{desc} · 完成",
            },
        )
    finally:
        if os.path.exists(part_file):
            try:
                os.remove(part_file)
            except OSError:
                logger.warning("清理临时下载分片文件失败：%s", part_file, exc_info=True)


def extract_best_audio_url(video_section):
    for audio_rate in video_section.get("bit_rate_audio") or []:
        audio_meta = (audio_rate or {}).get("audio_meta") or {}
        url_list = audio_meta.get("url_list") or {}
        if isinstance(url_list, dict):
            candidate = url_list.get("main_url") or url_list.get("backup_url")
            if candidate:
                return candidate
        elif isinstance(url_list, list):
            for candidate in url_list:
                if candidate:
                    return candidate
    return None


def download_video_task(
    video_id,
    force=False,
    progress_callback=None,
    work_gate=None,
    *,
    stop_event=None,
    resource_tracker=None,
    deadline=None,
    owner_id=None,
    save_dir=None,
    return_details=False,
):
    work_gate = work_gate or nullcontext
    video_id = str(video_id).strip()

    def _result(
        status: str,
        message: str,
        *,
        error: str | None = None,
        cancelled: bool = False,
        timed_out: bool = False,
        exit_verified: bool = True,
        output_file: str | None = None,
    ):
        if return_details:
            return DownloadTaskResult(
                status=status,
                message=message,
                error=error,
                cancelled=cancelled,
                timed_out=timed_out,
                exit_verified=exit_verified,
                output_file=output_file,
            )
        return status == "success", message

    if not AWEME_ID_RE.fullmatch(video_id):
        message = "视频 ID 格式不正确。"
        _report_progress(
            progress_callback,
            {
                "stage": "validate",
                "video_id": video_id,
                "progress": 0.0,
                "message": message,
            },
        )
        return _result("failed", message, error=message)

    if _is_cancelled(stop_event):
        message = "下载任务已取消，可点击重试。"
        _report_progress(
            progress_callback,
            {
                "stage": "cancelled",
                "video_id": video_id,
                "progress": 0.0,
                "message": message,
            },
        )
        return _result("interrupted", message, cancelled=True)

    if not force and is_already_downloaded(video_id):
        message = "视频已在下载记录中。"
        _report_progress(
            progress_callback,
            {
                "stage": "skip",
                "video_id": video_id,
                "progress": 1.0,
                "message": message,
            },
        )
        return _result("skipped", message)

    video_title = get_title_from_library(video_id) or "无标题"
    output_dir = str(save_dir or SAVE_DIR)
    detail_json = None
    last_error = ""
    ffmpeg_exit_verified = True
    video_temp = os.path.join(output_dir, f"temp_{video_id}_video.mp4")
    audio_temp = os.path.join(output_dir, f"temp_{video_id}_audio.m4a")
    merged_temp = os.path.join(output_dir, f"temp_{video_id}_merged.mp4")

    for path in (video_temp, audio_temp, merged_temp):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("清理历史临时文件失败：%s", path, exc_info=True)

    try:
        for attempt in range(1, DOWNLOAD_RETRY_COUNT + 1):
            if _is_cancelled(stop_event):
                raise DownloadCancelledError("下载任务已取消，可点击重试。")

            message = (
                f"正在拦截视频详情，尝试 {attempt}/{DOWNLOAD_RETRY_COUNT}"
            )
            _report_progress(
                progress_callback,
                {
                    "stage": "detail",
                    "video_id": video_id,
                    "attempt": attempt,
                    "max_attempts": DOWNLOAD_RETRY_COUNT,
                    "progress": 0.0,
                    "message": message,
                },
            )
            detail_json = None
            try:
                with browser_profile_guard(f"video-detail:{video_id}"):
                    if _is_cancelled(stop_event):
                        raise DownloadCancelledError("下载任务已取消，可点击重试。")
                    with work_gate():
                        session_context = StealthySession(
                            record_requests=True,
                            headless=False,
                            user_data_dir=USER_DATA_DIR,
                            network_idle=False,
                        )
                        session = session_context.__enter__()

                    try:
                        session.fetch(
                            f"https://www.douyin.com/video/{video_id}"
                        )
                        for _ in range(40):
                            if _interruptible_wait(0.5, stop_event):
                                raise DownloadCancelledError("下载任务已取消，可点击重试。")
                            for request in session.network.search(limit=None):
                                if (
                                    "/aweme/detail/" in request.url
                                    and f"aweme_id={video_id}" in request.url
                                    and request.status == 200
                                ):
                                    body = request.body
                                    if isinstance(body, bytes):
                                        body = body.decode("utf-8")
                                    detail_json = json.loads(body)
                                    break
                            if detail_json and "aweme_detail" in detail_json:
                                break
                    finally:
                        session_context.__exit__(*sys.exc_info())
            except DownloadCancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)

            if detail_json and "aweme_detail" in detail_json:
                break
            if attempt < DOWNLOAD_RETRY_COUNT:
                if _interruptible_wait(2, stop_event):
                    raise DownloadCancelledError("下载任务已取消，可点击重试。")

        if _is_cancelled(stop_event):
            raise DownloadCancelledError("下载任务已取消，可点击重试。")

        if not detail_json or "aweme_detail" not in detail_json:
            suffix = f" 最后错误：{last_error}" if last_error else ""
            message = (
                f"连续 {DOWNLOAD_RETRY_COUNT} 次未截获视频详情。{suffix}"
            ).strip()
            _report_progress(
                progress_callback,
                {
                    "stage": "detail_failed",
                    "video_id": video_id,
                    "progress": 0.0,
                    "message": message,
                },
            )
            return _result("failed", message, error=message)

        _report_progress(
            progress_callback,
            {
                "stage": "parse",
                "video_id": video_id,
                "progress": 0.0,
                "message": "已获取详情，正在解析媒体直链。",
            },
        )
        detail = detail_json.get("aweme_detail") or {}
        if video_title == "无标题":
            video_title = (
                str(detail.get("desc") or "无标题")
                .replace("\n", " ")
                .strip()
                or "无标题"
            )

        author_name = (detail.get("author") or {}).get("nickname", "未知作者")
        stats = detail.get("statistics") or {}
        video_section = detail.get("video") or {}

        best_video_url, best_score = None, (-1, -1)
        for rate in video_section.get("bit_rate") or []:
            play_addr = (rate or {}).get("play_addr") or {}
            height = int(play_addr.get("height", 0) or 0)
            is_h265 = int((rate or {}).get("is_h265", 0) or 0)
            url_list = play_addr.get("url_list") or []
            score = (height, 0 if is_h265 == 0 else -1)
            if url_list and score > best_score:
                best_score = score
                best_video_url = url_list[0]

        if not best_video_url:
            message = "未找到可用的视频直链。"
            _report_progress(
                progress_callback,
                {
                    "stage": "parse_failed",
                    "video_id": video_id,
                    "progress": 0.0,
                    "message": message,
                },
            )
            return _result("failed", message, error=message)

        best_audio_url = extract_best_audio_url(video_section)
        safe_title = (
            re.sub(r'[\\/*?:"<>|\n\r#]', "", video_title).strip(" .")[:30]
            or video_id[-6:]
        )
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(
            output_dir,
            f"{safe_title}_{video_id[-6:]}.mp4",
        )

        def media_progress(phase):
            def callback(payload):
                enriched = dict(payload)
                enriched.update(
                    {
                        "video_id": video_id,
                        "phase": phase,
                    }
                )
                _report_progress(progress_callback, enriched)

            return callback

        download_media(
            best_video_url,
            video_temp,
            desc="视频流",
            progress_callback=media_progress("video"),
            stop_event=stop_event,
        )
        if best_audio_url:
            download_media(
                best_audio_url,
                audio_temp,
                desc="音频流",
                progress_callback=media_progress("audio"),
                stop_event=stop_event,
            )
            if _is_cancelled(stop_event):
                raise DownloadCancelledError("下载任务已取消，可点击重试。")
            _report_progress(
                progress_callback,
                {
                    "stage": "merge",
                    "video_id": video_id,
                    "progress": 1.0,
                    "message": "正在合并音视频。",
                },
            )
            merge_result = merge_video_and_audio(
                video_temp,
                audio_temp,
                merged_temp,
                stop_event=stop_event,
                deadline=deadline,
                resource_tracker=resource_tracker,
                owner=owner_id or f"download:{video_id}",
            )
            if isinstance(merge_result, FFmpegRunResult):
                if not merge_result.exit_verified:
                    ffmpeg_exit_verified = False
                    raise FFmpegExitUnconfirmedError(
                        merge_result.error or "FFmpeg 进程退出未确认，禁止提交文件。",
                        result=merge_result,
                    )
                if merge_result.cancelled or _is_cancelled(stop_event):
                    raise DownloadCancelledError("下载任务已取消，可点击重试。")
                if merge_result.returncode != 0 or not merge_result.output_valid:
                    raise FFmpegProcessError(
                        merge_result.error or "FFmpeg 合并输出校验失败。",
                        result=merge_result,
                    )
            else:
                if _is_cancelled(stop_event):
                    raise DownloadCancelledError("下载任务已取消，可点击重试。")
                output_ok, output_detail = validate_merged_output(merged_temp)
                if not output_ok:
                    raise FFmpegProcessError(output_detail)

            if _is_cancelled(stop_event):
                raise DownloadCancelledError("下载任务已取消，可点击重试。")
            os.replace(merged_temp, output_file)
        else:
            if _is_cancelled(stop_event):
                raise DownloadCancelledError("下载任务已取消，可点击重试。")
            output_ok, output_detail = validate_merged_output(video_temp)
            if not output_ok:
                raise RuntimeError(output_detail)
            os.replace(video_temp, output_file)

        record = {
            "视频ID": video_id,
            "作者昵称": author_name,
            "视频标题": video_title,
            "点赞量": stats.get("digg_count", 0),
            "评论量": stats.get("comment_count", 0),
            "收藏量": stats.get("collect_count", 0),
            "转发量": stats.get("share_count", 0),
        }
        _report_progress(
            progress_callback,
            {
                "stage": "record",
                "video_id": video_id,
                "progress": 1.0,
                "message": "正在写入下载记录。",
            },
        )
        upsert_analytics_record(record)
        message = f"视频已保存：{output_file}"
        _report_progress(
            progress_callback,
            {
                "stage": "complete",
                "video_id": video_id,
                "progress": 1.0,
                "message": message,
            },
        )
        return _result("success", message, output_file=output_file)
    except FFmpegExitUnconfirmedError as exc:
        ffmpeg_exit_verified = False
        message = f"FFmpeg 退出未确认，已保留临时文件防止占用冲突：{exc}"
        logger.error("%s", message)
        _report_progress(
            progress_callback,
            {
                "stage": "error",
                "video_id": video_id,
                "progress": 0.0,
                "message": message,
            },
        )
        return _result("failed", message, error=message, exit_verified=False)
    except (DownloadCancelledError, FFmpegCancelledError) as exc:
        message = str(exc) or "下载任务已取消，可点击重试。"
        _report_progress(
            progress_callback,
            {
                "stage": "cancelled",
                "video_id": video_id,
                "progress": 0.0,
                "message": message,
            },
        )
        return _result("interrupted", message, cancelled=True, exit_verified=True)
    except FFmpegTimeoutError as exc:
        message = f"音视频合并超时：{exc}"
        _report_progress(
            progress_callback,
            {
                "stage": "error",
                "video_id": video_id,
                "progress": 0.0,
                "message": message,
            },
        )
        return _result("failed", message, error=message, timed_out=True, exit_verified=True)
    except Exception as exc:
        message = f"下载或合并失败：{exc}"
        _report_progress(
            progress_callback,
            {
                "stage": "error",
                "video_id": video_id,
                "progress": 0.0,
                "message": message,
            },
        )
        return _result("failed", message, error=message)
    finally:
        if ffmpeg_exit_verified:
            for path in (video_temp, audio_temp, merged_temp):
                _remove_temp_file_with_retry(path)
        else:
            logger.warning(
                "FFmpeg 退出未确认，跳过临时文件删除（video_id=%s）。",
                video_id,
            )
