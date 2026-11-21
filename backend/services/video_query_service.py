import math

import pandas as pd

from backend.repositories import get_analytics_df, get_downloaded_ids, get_video_library_df
from backend.services.runtime import RuntimeServices


VIDEO_STATUS_META = {
    "pending": ("待下载", "可加入队列"),
    "downloaded": ("已下载", "已完成"),
    "running": ("下载中", "正在下载"),
    "failed": ("下载失败", "可在队列重试"),
    "interrupted": ("已中断", "可重试"),
    "success": ("已下载", "已完成"),
    "skipped": ("已存在", "无需下载"),
    "queued": ("队列中", "等待开始"),
}


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


class VideoQueryService:
    def __init__(self, runtime: RuntimeServices) -> None:
        self.runtime = runtime

    def _enriched_library(self) -> pd.DataFrame:
        library = get_video_library_df().copy()
        if library.empty:
            return library

        library["_library_index"] = range(len(library))
        downloaded_ids = get_downloaded_ids()
        tasks = {
            str(task.get("video_id", "")).strip(): task
            for task in self.runtime.task_manager.get_tasks()
            if task.get("video_id")
        }

        enriched = []
        for _, row in library.iterrows():
            video_id = _text(row["视频ID"])
            title = _text(row["标题"]) or "无标题"
            task = tasks.get(video_id)

            if video_id in downloaded_ids:
                status = "downloaded"
            elif task:
                status = str(task.get("status") or "queued")
                if status == "waiting":
                    status = "queued"
            else:
                status = "pending"

            status_label, action_label = VIDEO_STATUS_META.get(
                status,
                (status, ""),
            )
            enriched.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "status": status,
                    "status_label": status_label,
                    "action_label": action_label,
                    "task_id": task.get("task_id") if task else None,
                    "_library_index": int(row["_library_index"]),
                }
            )

        return pd.DataFrame(enriched)

    def list_videos(
        self,
        *,
        keyword: str = "",
        status_filter: str = "all",
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "library",
        sort_order: str = "asc",
    ) -> dict:
        frame = self._enriched_library()
        if not frame.empty:
            keyword = keyword.strip()
            if keyword:
                mask = (
                    frame["video_id"].str.contains(keyword, case=False, na=False)
                    | frame["title"].str.contains(keyword, case=False, na=False)
                )
                frame = frame[mask]

            if status_filter != "all":
                frame = frame[frame["status"] == status_filter]

            sort_column = "_library_index" if sort_by == "library" else sort_by
            if sort_column not in frame.columns:
                sort_column = "_library_index"
            frame = frame.sort_values(
                by=sort_column,
                ascending=sort_order != "desc",
                kind="stable",
            )

        total = len(frame)
        total_pages = math.ceil(total / page_size) if total else 0
        start = (page - 1) * page_size
        end = start + page_size
        page_frame = frame.iloc[start:end] if start < total else frame.iloc[0:0]

        items = []
        if not page_frame.empty:
            items = page_frame[
                [
                    "video_id",
                    "title",
                    "status",
                    "status_label",
                    "action_label",
                    "task_id",
                ]
            ].to_dict(orient="records")

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    def summary(self) -> dict:
        library_total = len(get_video_library_df())
        downloaded_total = len(get_downloaded_ids())
        counts = self.runtime.task_manager.counts()
        return {
            "total": library_total,
            "downloaded_total": downloaded_total,
            "pending_total": max(library_total - downloaded_total, 0),
            "waiting_tasks": counts.get("waiting", 0),
            "running_tasks": counts.get("running", 0),
            "failed_tasks": counts.get("failed", 0),
        }

    def analytics(self, keyword: str = "") -> dict:
        frame = get_analytics_df()
        if not frame.empty and keyword.strip():
            text = keyword.strip()
            mask = pd.Series(False, index=frame.index)
            for column in ("视频ID", "视频标题", "作者昵称"):
                if column in frame.columns:
                    mask = mask | frame[column].fillna("").astype(str).str.contains(
                        text,
                        case=False,
                        na=False,
                    )
            frame = frame[mask]

        items = []
        for row in frame.itertuples(index=False):
            items.append(
                {
                    "video_id": _text(row.视频ID),
                    "author_name": _text(row.作者昵称),
                    "video_title": _text(row.视频标题),
                    "digg_count": _text(row.点赞量),
                    "comment_count": _text(row.评论量),
                    "collect_count": _text(row.收藏量),
                    "share_count": _text(row.转发量),
                }
            )
        return {"items": items, "total": len(items)}

    def list_recent(self, page_size: int = 20) -> dict:
        return self.list_videos(
            page=1,
            page_size=page_size,
            sort_by="library",
            sort_order="desc",
        )
