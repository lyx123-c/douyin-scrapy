import pandas as pd

from config import ANALYTICS_COLUMNS, CSV_FILE, VIDEO_COLUMNS, VIDEO_TITLE_CSV

from .csv_store import csv_write_lock, read_csv_safe, write_csv_atomic


VIDEO_LIBRARY_PATH = VIDEO_TITLE_CSV
ANALYTICS_PATH = CSV_FILE


def load_video_library_df(strict=False):
    frame = read_csv_safe(VIDEO_LIBRARY_PATH, VIDEO_COLUMNS, strict=strict)
    if frame.empty:
        return pd.DataFrame(columns=VIDEO_COLUMNS)

    frame = frame[VIDEO_COLUMNS].copy()
    frame = frame[frame["视频ID"].ne("")]
    frame["视频ID"] = frame["视频ID"].astype(str).str.strip()
    frame["标题"] = frame["标题"].fillna("无标题").astype(str).str.strip()
    frame.loc[frame["标题"].eq(""), "标题"] = "无标题"
    return frame.drop_duplicates(subset="视频ID", keep="last").reset_index(drop=True)


def load_analytics_df(strict=False):
    frame = read_csv_safe(ANALYTICS_PATH, ANALYTICS_COLUMNS, strict=strict)
    if frame.empty:
        return pd.DataFrame(columns=ANALYTICS_COLUMNS)
    frame["视频ID"] = frame["视频ID"].astype(str).str.strip()
    return frame.reset_index(drop=True)


def get_video_library_df():
    return load_video_library_df(strict=False)


def get_analytics_df():
    return load_analytics_df(strict=False)


def get_downloaded_video_ids():
    frame = get_analytics_df()
    if frame.empty or "视频ID" not in frame.columns:
        return set()
    return {
        str(video_id).strip()
        for video_id in frame["视频ID"].tolist()
        if str(video_id).strip()
    }


def get_downloaded_ids():
    return get_downloaded_video_ids()


def is_already_downloaded(video_id):
    return str(video_id).strip() in get_downloaded_video_ids()


def get_title_from_library(video_id):
    frame = get_video_library_df()
    if frame.empty:
        return None

    matches = frame[frame["视频ID"] == str(video_id)]
    if matches.empty:
        return None

    title = str(matches.iloc[-1]["标题"]).strip()
    return title if title and title != "无标题" else None


def merge_video_library(rows):
    """Merge rows, update duplicate titles, and atomically save the library."""
    if not rows:
        return 0

    incoming = pd.DataFrame(rows)
    for column in VIDEO_COLUMNS:
        if column not in incoming.columns:
            incoming[column] = ""
    incoming = incoming[VIDEO_COLUMNS].copy()
    incoming["视频ID"] = incoming["视频ID"].astype(str).str.strip()
    incoming = incoming[incoming["视频ID"].ne("")]
    incoming["标题"] = incoming["标题"].fillna("无标题").astype(str).str.strip()
    incoming.loc[incoming["标题"].eq(""), "标题"] = "无标题"
    if incoming.empty:
        return 0

    with csv_write_lock(VIDEO_LIBRARY_PATH):
        current = load_video_library_df(strict=True)
        combined = pd.concat([current, incoming], ignore_index=True)
        combined = combined.drop_duplicates(subset="视频ID", keep="last")
        write_csv_atomic(combined.reset_index(drop=True), VIDEO_LIBRARY_PATH)

    return len(incoming)


def update_video_title(video_id, title):
    video_id = str(video_id).strip()
    if not video_id:
        return 0
    return merge_video_library([{"视频ID": video_id, "标题": title}])


def upsert_analytics_record(record):
    """Insert or replace one download record while preserving all other rows."""
    normalized = {
        column: "" if record.get(column) is None else str(record.get(column, ""))
        for column in ANALYTICS_COLUMNS
    }

    with csv_write_lock(ANALYTICS_PATH):
        frame = load_analytics_df(strict=True)
        if not frame.empty:
            frame = frame[
                frame["视频ID"].astype(str).str.strip() != str(record["视频ID"])
            ]
        combined = pd.concat([frame, pd.DataFrame([normalized])], ignore_index=True)
        write_csv_atomic(combined[ANALYTICS_COLUMNS], ANALYTICS_PATH)

    return normalized
