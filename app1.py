import logging
import time

import pandas as pd
import streamlit as st

from config import (
    AWEME_ID_RE,
    CAPTURE_UI_POLL_SECONDS,
    DOWNLOAD_UI_POLL_SECONDS,
    TASK_LABELS,
)

from backend_client.client import (
    BackendClientError,
    create_backend_client,
)

logger = logging.getLogger(__name__)


# ================= 页面配置 =================
st.set_page_config(page_title="抖音视频工作台", page_icon=":material/movie:", layout="wide",
                   initial_sidebar_state="collapsed")


st.markdown(
    """
    <style>
    :root {
        --app-bg: #eef2f5;
        --panel: #ffffff;
        --line: #d7dee5;
        --line-strong: #bcc7d2;
        --text: #17212b;
        --muted: #64717f;
        --accent: #0f766e;
        --warning: #a15c00;
        --danger: #b42318;
    }

    html, body, [class*="css"] {
        font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
        color: var(--text);
    }

    [data-testid="stAppViewContainer"] {
        background: var(--app-bg);
    }

    [data-testid="stHeader"] {
        display: none;
    }

    [data-testid="stToolbar"],
    #MainMenu,
    footer {
        visibility: hidden;
    }

    [data-testid="stSkillsNudge"] {
        display: none;
    }

    .block-container {
        max-width: 1680px;
        padding: 0.65rem 1.15rem 1.5rem;
    }

    [data-testid="stVerticalBlock"] {
        gap: 0.55rem;
    }

    h1, h2, h3 {
        letter-spacing: 0;
        color: var(--text);
    }

    .st-key-toolbar {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.72rem 0.9rem 0.62rem;
    }

    .st-key-capture_tools,
    .st-key-download_tools,
    .st-key-library_tools,
    .st-key-capture_action_bar,
    .st-key-library_action_bar {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.72rem 0.82rem 0.62rem;
    }

    .st-key-download_dock {
        position: relative;
        width: 100% !important;
        max-width: 100%;
        margin-top: 0.9rem;
        box-sizing: border-box;
        overflow: hidden;
        background: var(--panel);
        border: 1px solid var(--line-strong);
        border-radius: 8px;
        padding: 0.62rem 0.82rem 0.55rem;
        box-shadow: 0 2px 8px rgba(23, 33, 43, 0.06);
    }

    .st-key-download_dock [data-testid="stExpander"] {
        border: 1px solid var(--line);
        border-radius: 6px;
        background: #fafbfc;
        max-height: 38vh;
        overflow-y: auto;
    }

    .app-title-row {
        display: flex;
        align-items: center;
        gap: 0.65rem;
        flex-wrap: wrap;
        min-height: 2rem;
    }

    .app-title {
        font-size: 1.35rem;
        line-height: 1.25;
        font-weight: 750;
        color: var(--text);
    }

    .app-meta {
        margin-top: 0.18rem;
        color: var(--muted);
        font-size: 0.82rem;
        line-height: 1.35;
    }

    .status-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.38rem;
        min-height: 1.5rem;
        padding: 0.13rem 0.5rem;
        border-radius: 999px;
        font-size: 0.76rem;
        font-weight: 650;
        border: 1px solid transparent;
        white-space: nowrap;
    }

    .status-chip::before {
        content: "";
        width: 0.43rem;
        height: 0.43rem;
        border-radius: 50%;
        background: currentColor;
    }

    .status-chip.ok {
        color: #067647;
        background: #e8f7ef;
        border-color: #b7e4c8;
    }

    .status-chip.warn {
        color: #a15c00;
        background: #fff4df;
        border-color: #f0d39a;
    }

    .dock-title {
        font-size: 0.9rem;
        font-weight: 750;
        color: var(--text);
    }

    .dock-meta {
        color: var(--muted);
        font-size: 0.77rem;
        line-height: 1.35;
    }

    .selection-label {
        color: var(--muted);
        font-size: 0.82rem;
        padding-top: 0.38rem;
    }

    .stButton button {
        min-height: 2.15rem;
        border-radius: 6px;
        font-weight: 650;
        letter-spacing: 0;
    }

    .stButton button[kind="primary"] {
        background: var(--accent);
        border-color: var(--accent);
    }

    .stButton button[kind="primary"]:hover {
        background: #0b5f59;
        border-color: #0b5f59;
    }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 6px;
        overflow: hidden;
    }

    [data-testid="stTextInput"] input,
    [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        border-radius: 6px;
    }

    [data-testid="stMetric"] {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 6px;
        padding: 0.45rem 0.65rem;
    }

    [data-testid="stAlert"] {
        border-radius: 6px;
    }

    @media (max-width: 760px) {
        .block-container {
            padding: 0.45rem 0.55rem 1.25rem;
        }

        .st-key-dock_current_state,
        .st-key-dock_progress_state {
            display: none;
        }

        .st-key-download_dock [data-testid="stColumn"]:has(.st-key-dock_current_state),
        .st-key-download_dock [data-testid="stColumn"]:has(.st-key-dock_progress_state) {
            display: none !important;
        }

        .st-key-download_dock > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
        }

        .st-key-download_dock [data-testid="stColumn"] {
            min-width: 0 !important;
        }

        .st-key-download_dock [data-testid="stColumn"]:first-child {
            flex: 1 1 auto !important;
        }

        .st-key-download_dock [data-testid="stColumn"]:last-child {
            flex: 0 0 132px !important;
        }

        .st-key-download_dock {
            padding: 0.5rem 0.55rem;
        }

        .app-title {
            font-size: 1.15rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ================= 初始化 Session State =================
STATE_DEFAULTS = {
    "collected_ids": [],
    "download_id_input": "",
    "download_start_requested": False,
    "library_table_epoch": 0,
    "notice": None,
    "workspace_section": "抓取",
}

for state_key, default_value in STATE_DEFAULTS.items():
    if state_key not in st.session_state:
        # 如果默认值是可变的(list/dict)，使用 copy 防止跨会话污染
        st.session_state[state_key] = (
            default_value.copy() if isinstance(default_value, (list, dict)) else default_value
        )

@st.cache_resource
def get_backend_client():
    return create_backend_client()


def backend_client():
    return get_backend_client()


def auth_status_data():
    return st.session_state.get("auth_status") or {}


def video_summary_data():
    return st.session_state.get("video_summary") or {}


def capture_status_data():
    return st.session_state.get("capture_status") or {}


def downloads_snapshot_data():
    return st.session_state.get("downloads_snapshot") or {}


def download_queue_status_data():
    return downloads_snapshot_data().get("queue") or {}


STATUS_FILTER_MAP = {
    "全部": "all",
    "待下载": "pending",
    "已下载": "downloaded",
    "队列中": "queued",
    "下载失败": "failed",
}


def video_items_frame(items):
    rows = [
        {
            "视频ID": item.get("video_id", ""),
            "标题": item.get("title", "无标题"),
            "状态": item.get("status_label", ""),
            "操作": item.get("action_label", ""),
        }
        for item in items
    ]
    return pd.DataFrame(rows, columns=["视频ID", "标题", "状态", "操作"])


def analytics_items_frame(items):
    rows = [
        {
            "视频ID": item.get("video_id", ""),
            "作者昵称": item.get("author_name", ""),
            "视频标题": item.get("video_title", ""),
            "点赞量": item.get("digg_count", ""),
            "评论量": item.get("comment_count", ""),
            "收藏量": item.get("collect_count", ""),
            "转发量": item.get("share_count", ""),
        }
        for item in items
    ]
    return pd.DataFrame(
        rows,
        columns=["视频ID", "作者昵称", "视频标题", "点赞量", "评论量", "收藏量", "转发量"],
    )


def set_notice(kind, message):
    st.session_state.notice = {"kind": kind, "message": message}


def render_notice():
    notice = st.session_state.notice
    if not notice: return
    kind, message = notice.get("kind"), notice.get("message", "")
    if kind == "success":
        st.success(message)
    elif kind == "warning":
        st.warning(message)
    elif kind == "error":
        st.error(message)
    else:
        st.info(message)
    st.session_state.notice = None


def clear_table_state(key):
    st.session_state.pop(key, None)


def selected_rows_from_state(table_state):
    try:
        return list(table_state.selection.rows)
    except Exception:
        return []


def reset_library_table_selection():
    st.session_state.library_table_epoch += 1


# ================= 队列工具 =================
def snapshot_tasks():
    return downloads_snapshot_data().get("items", [])


def queue_entry(video_id):
    video_id = str(video_id).strip()
    for task in reversed(snapshot_tasks()):
        if task.get("task_type") == "download" and str(task.get("video_id", "")) == video_id:
            return task
    return None


def queue_counts():
    return downloads_snapshot_data().get("counts", {})


def queue_immediate_download(video_id, force=False):
    task = queue_entry(video_id)
    title = task.get("title", "无标题") if task else "无标题"
    try:
        result = backend_client().immediate_download(video_id, title, force)
    except BackendClientError as exc:
        set_notice("error", f"Backend 请求失败：{exc}")
        return False

    if result.get("queued"):
        set_notice("success", "已加入下载队列，将立即开始下载。")
        return True
    if result.get("reason") == "already_downloaded":
        set_notice("info", "该视频已有下载记录，可勾选“强制重新下载”。")
    elif result.get("reason") == "running":
        set_notice("info", "该视频正在下载，请勿重复加入。")
    else:
        set_notice("warning", "任务未加入下载队列。")
    return False


def add_to_queue(records):
    items = [
        {
            "video_id": str(record.get("id", "")).strip(),
            "title": str(record.get("title", "")).strip() or "无标题",
        }
        for record in records
    ]
    try:
        result = backend_client().queue_items(items)
    except BackendClientError as exc:
        set_notice("error", f"加入队列失败：{exc}")
        st.rerun()
    added = int(result.get("created_count", 0))
    if added:
        set_notice("success", f"已加入 {added} 个视频，任务会在手动开始后依次下载。")
    else:
        set_notice("info", "所选视频已经在队列中，没有重复加入。")
    st.rerun()


def task_frame():
    tasks = snapshot_tasks()
    if not tasks:
        return pd.DataFrame(columns=["视频ID", "标题", "状态", "信息", "更新时间"])
    rows = []
    for task in tasks:
        rows.append({"视频ID": task.get("video_id", ""), "标题": task.get("title", "无标题"),
                     "状态": TASK_LABELS.get(task.get("status"), task.get("status", "")),
                     "信息": task.get("message", ""), "更新时间": task.get("updated_at", "")})
    return pd.DataFrame(rows)


def task_options():
    return {f'{task["video_id"][-6:]} · {task["title"][:28]} · {TASK_LABELS.get(task["status"], task["status"])}': task[
        "task_id"] for task in reversed(snapshot_tasks())}












def start_download_queue_worker():
    try:
        result = backend_client().start_downloads()
    except BackendClientError as exc:
        set_notice("error", f"下载队列启动失败：{exc}")
        return False
    return bool(result.get("started") or result.get("running"))


@st.fragment(run_every=DOWNLOAD_UI_POLL_SECONDS)
def render_download_queue_progress():
    try:
        downloads = backend_client().downloads_snapshot()
        st.session_state.downloads_snapshot = downloads
    except BackendClientError as exc:
        st.error(f"Backend 服务不可用：{exc}")
        return

    tasks = downloads.get("items", [])
    counts = downloads.get("counts", {})

    active = next(
        (
            t
            for t in tasks
            if t.get("status") == "running"
            and t.get("task_type") == "download"
        ),
        None,
    )

    total = sum(counts.values())

    completed = (
        counts.get("success", 0)
        + counts.get("failed", 0)
        + counts.get("skipped", 0)
    )

    interrupted = counts.get("interrupted", 0)

    with st.status(
        "正在执行下载队列",
        expanded=True,
    ) as run_status:

        if active:
            run_status.write(
                active.get("message")
                or "正在执行下载任务..."
            )

        elif interrupted:
            run_status.write(
                f"有 {interrupted} 个任务上次执行中断"
            )

        else:
            run_status.write(
                "正在领取下载任务..."
            )

        progress = (
            completed / total
            if total
            else 0.0
        )

        st.progress(
            progress,
            text=f"完成 {completed}/{total}",
        )

    # 只有队列从 running → 完成以后，
    # 才需要整页重新渲染。
    if (
        not downloads.get("queue", {}).get("running", False)
        and completed > 0
    ):
        st.rerun()

# ================= 通用界面 =================
def refresh_workbench_data():
    st.cache_data.clear()
    auth_status = backend_client().get_auth_status()
    summary = backend_client().get_video_summary()
    analytics = backend_client().get_analytics()
    downloads = backend_client().downloads_snapshot()
    st.session_state.auth_status = auth_status
    st.session_state.video_summary = summary
    st.session_state.downloads_snapshot = downloads
    counts = downloads.get("counts", {})
    message = (
        f"已刷新：视频库 {summary.get('total', 0)} 条，"
        f"已下载 {analytics.get('total', 0)} 条，"
        f"队列 {len(downloads.get('items', []))} 条（待处理 {counts.get('waiting', 0)}，"
        f"下载中 {counts.get('running', 0)}，失败 {counts.get('failed', 0)}）。"
    )
    logger.info(message)
    set_notice("success", message)


def open_video_directory():
    return backend_client().open_save_directory()


def perform_douyin_login():
    return backend_client().login()


def render_toolbar(library_count, downloaded_count, auth_status, capture_status):
    login_ready = bool((auth_status or {}).get("login_ready"))
    capture_running = bool((capture_status or {}).get("status") == "running")
    download_running = bool(download_queue_status_data().get("running"))
    with st.container(key="toolbar"):
        title_col, login_col, folder_col, refresh_col = st.columns([6.0, 1.2, 1.35, 1.0], vertical_alignment="center")
        with title_col:
            st.markdown(
                f'<div class="app-title-row"><span class="app-title">抖音视频工作台</span><span class="status-chip {"ok" if login_ready else "warn"}">{"本地会话已保存" if login_ready else "尚未登录"}</span></div><div class="app-meta">视频库 {library_count} 条 · 已下载 {downloaded_count} 条</div>',
                unsafe_allow_html=True)
        with login_col: login_clicked = st.button("重新登录" if login_ready else "登录抖音", icon=":material/login:",
                                                  disabled=capture_running or download_running,
                                                  width="stretch", key="toolbar_login_btn")
        with folder_col: open_folder_clicked = st.button("打开目录", icon=":material/folder_open:", width="stretch",
                                                         key="toolbar_folder_btn")
        with refresh_col: refresh_clicked = st.button("刷新", icon=":material/refresh:", width="stretch",
                                                      key="toolbar_refresh_btn")

    if login_clicked:
        try:
            with st.status(
                "请在弹出的浏览器中完成登录...",
                expanded=True,
            ) as status:
                login_result = perform_douyin_login()
                status.update(label="登录流程结束", state="complete", expanded=False)
            set_notice("success", "登录流程已结束，浏览器 Profile 已保存。")
            logger.info("抖音重新登录流程完成：%s", login_result.get("profile_saved"))
        except Exception as exc:
            logger.exception("抖音重新登录失败")
            set_notice("error", f"登录失败：{exc}")
        st.rerun()
    if open_folder_clicked:
        try:
            result = open_video_directory()
            set_notice("success", f"已在资源管理器中打开视频目录：{result.get('path', '')}")
            logger.info("已打开视频目录：%s", result.get("path", ""))
        except Exception as exc:
            logger.exception("打开视频目录失败")
            set_notice("error", f"无法打开目录：{exc}")
        st.rerun()
    if refresh_clicked:
        try:
            refresh_workbench_data()
        except Exception as exc:
            logger.exception("刷新工作台数据失败")
            set_notice("error", f"刷新失败：{exc}")
        st.rerun()


def render_download_dock():
    downloads = downloads_snapshot_data()
    counts = queue_counts()
    tasks = downloads.get("items", [])
    queue_status = downloads.get("queue", {})
    download_running = bool(queue_status.get("running"))
    download_stopping = bool(queue_status.get("stopping"))
    if not download_running:
        if counts.get("interrupted", 0):
            st.warning(
                f"检测到 {counts['interrupted']} 个上次中断的任务，请检查后点击重试。"
            )
        elif counts.get("waiting", 0):
            st.info(
                f"当前有 {counts['waiting']} 个等待任务，可以点击开始队列继续执行。"
            )
    capture_running = bool(capture_status_data().get("status") == "running")
    with st.container(key="download_dock"):
        title_col, current_col, progress_col, start_col = st.columns([1.55, 3.0, 1.45, 1.05],
                                                                     vertical_alignment="center")
        with title_col:
            st.markdown(
                f'<div class="dock-title">下载任务</div><div class="dock-meta">待处理 {counts["waiting"]} · 完成 {counts["success"] + counts["skipped"]} · 失败 {counts["failed"]}</div>',
                unsafe_allow_html=True)
        with current_col:
            with st.container(key="dock_current_state"): current_placeholder = st.empty()
        with progress_col:
            with st.container(key="dock_progress_state"): progress_placeholder = st.empty()

        if download_running:
            current_placeholder.info("正在准备下载任务");
            progress_placeholder.progress(0.0, text="正在启动")
        elif counts["waiting"]:
            current_placeholder.write(f'{counts["waiting"]} 个任务等待开始');
            progress_placeholder.progress(0.0, text=f'待处理 {counts["waiting"]}')
        else:
            current_placeholder.caption("队列空闲");
            progress_placeholder.caption("暂无待处理任务")

        with start_col:
            start_clicked = st.button("开始队列", icon=":material/play_arrow:", type="primary", width="stretch",
                                      disabled=counts["waiting"] == 0 or download_running or capture_running,
                                      key="dock_start_btn")
            stop_clicked = st.button(
                "停止队列",
                icon=":material/stop_circle:",
                width="stretch",
                disabled=not download_running or download_stopping,
                key="dock_stop_btn",
            )
        if start_clicked:
            st.session_state.download_start_requested = True
            st.rerun()
        if stop_clicked:
            try:
                backend_client().stop_downloads()
                set_notice("warning", "将在当前任务完成后停止下载队列。")
            except BackendClientError as exc:
                set_notice("info", str(exc))
            st.rerun()

        with st.expander(f"任务队列 · {len(tasks)} 条", expanded=False):
            st.dataframe(task_frame(), hide_index=True, width="stretch",
                         height=min(260, max(96, len(tasks) * 36 + 40)), key="dock_task_table")
            if tasks:
                options = task_options()
                selected_label = st.selectbox("选择任务", options=list(options.keys()), key="queue_task_selector")
                selected_task_id = options[selected_label]
                selected_task = next((task for task in tasks if task["task_id"] == selected_task_id), None)
                can_remove_task = selected_task is not None and selected_task.get("status") != "running"
                action_col, clear_col, remove_col = st.columns([1.0, 1.0, 1.0])
                with action_col:
                    retry_clicked = st.button("重试失败任务", width="stretch",
                                              disabled=not selected_task or selected_task["status"] not in ("failed", "interrupted"),
                                              key="dock_retry_btn")
                with remove_col:
                    remove_clicked = st.button("移除选中任务", width="stretch",
                                               disabled=not can_remove_task,
                                               key="dock_remove_btn")
                with clear_col:
                    clear_clicked = st.button("清除已完成", width="stretch", disabled=not any(
                        t["status"] in ("success", "skipped") for t in tasks), key="dock_clear_btn")
                if retry_clicked and selected_task:
                    try:
                        backend_client().retry_task(selected_task_id)
                        set_notice("info", "已重新加入待下载")
                    except BackendClientError as exc:
                        set_notice("error", f"重试失败：{exc}")
                    st.rerun()
                if remove_clicked and can_remove_task:
                    st.session_state.pop("queue_task_selector", None)
                    try:
                        backend_client().delete_task(selected_task_id)
                        set_notice("success", "已移除选中任务")
                    except BackendClientError as exc:
                        set_notice("warning", f"无法删除任务：{exc}")
                    st.rerun()
                if clear_clicked:
                    try:
                        backend_client().clear_completed()
                        set_notice("success", "已清除完成任务")
                    except BackendClientError as exc:
                        set_notice("error", f"清理失败：{exc}")
                    st.rerun()


def preview_frame(frame, downloaded_ids):
    if frame.empty: return pd.DataFrame(columns=["视频ID", "标题", "状态", "操作"])
    result = frame[["视频ID", "标题"]].copy()
    statuses, actions = [], []
    for video_id in result["视频ID"].astype(str):
        task = queue_entry(video_id)
        if video_id in downloaded_ids:
            statuses.append("已下载"); actions.append("已完成")
        elif task and task["status"] == "running":
            statuses.append("下载中"); actions.append("正在下载")
        elif task and task["status"] == "failed":
            statuses.append("下载失败"); actions.append("可在队列重试")
        elif task and task["status"] == "interrupted":
            statuses.append("已中断"); actions.append("可重试")
        elif task and task["status"] == "success":
            statuses.append("已下载"); actions.append("已完成")
        elif task and task["status"] == "skipped":
            statuses.append("已存在"); actions.append("无需下载")
        elif task:
            statuses.append("队列中"); actions.append("等待开始")
        else:
            statuses.append("待下载"); actions.append("可加入队列")
    result["状态"], result["操作"] = statuses, actions
    return result


def render_selection_bar(container_key, selected_count, table_key, records, label="加入队列"):
    with st.container(key=container_key):
        label_col, action_col, clear_col = st.columns([5.5, 1.25, 1.0], vertical_alignment="center")
        with label_col: st.markdown(f'<div class="selection-label">已选择 <b>{selected_count}</b> 个视频</div>',
                                    unsafe_allow_html=True)
        with action_col: add_clicked = st.button(label, icon=":material/add:", type="primary", width="stretch",
                                                 key=f"{container_key}_add_btn")
        with clear_col: clear_clicked = st.button("清空选择", icon=":material/close:", width="stretch",
                                                  key=f"{container_key}_clear_btn")
    if add_clicked: clear_table_state(table_key); add_to_queue(records)
    if clear_clicked: clear_table_state(table_key); st.rerun()


def render_library_queue_action(placeholder, table_key, records):
    selected_count = len(records)
    button_label = f"加入下载队列（{selected_count}）" if selected_count else "加入下载队列"

    with placeholder.container():
        status_col, action_col = st.columns([1.7, 3.0], vertical_alignment="center")
        with status_col:
            st.caption(f"已选择 {selected_count} 个视频" if selected_count else "尚未选择视频")
        with action_col:
            add_col, clear_col = st.columns([4.0, 1.25], vertical_alignment="center")
            with add_col:
                add_clicked = st.button(
                    button_label,
                    icon=":material/add:",
                    type="primary" if selected_count else "secondary",
                    width="stretch",
                    disabled=selected_count == 0,
                    key="library_action_bar_add_btn",
                )
            with clear_col:
                clear_clicked = st.button(
                    "清空选择",
                    icon=":material/close:",
                    width="stretch",
                    disabled=selected_count == 0,
                    key="library_action_bar_clear_btn",
                )

    if add_clicked and records:
        reset_library_table_selection()
        add_to_queue(records)
    if clear_clicked:
        reset_library_table_selection()
        st.rerun()






def render_capture_tab(downloaded_ids, capture_status):
    mode_options = ["推荐页", "博主主页", "精选首页", "搜索页"]
    mode_map = {"推荐页": ("https://www.douyin.com/?recommend=1", "recommend"), "博主主页": ("", "user"),
                "精选首页": ("https://www.douyin.com/jingxuan", "jingxuan"), "搜索页": ("", "search")}
    mode = "recommend"
    capture = capture_status
    capture_running = bool(capture and capture.get("status") == "running")
    download_running = bool(download_queue_status_data().get("running"))
    capture_stopping = bool(capture and capture.get("stopping"))
    with st.container(key="capture_tools"):
        mode_col, target_col, action_col, stop_col = st.columns([1.05, 4.2, 1.1, 1.1], vertical_alignment="bottom")
        with mode_col:
            mode_label = st.selectbox("抓取模式", mode_options, key="capture_mode")
        default_url, mode = mode_map[mode_label]
        with target_col:
            if mode_label == "博主主页":
                target_url = st.text_input("博主主页链接", placeholder="粘贴抖音博主主页地址",
                                           key="capture_user_url").strip()
            elif mode_label == "搜索页":
                keyword = st.text_input("搜索关键词", placeholder="输入关键词或话题",
                                        key="capture_search_keyword").strip()
                target_url = f"https://www.douyin.com/search/{keyword}?type=video" if keyword else ""
            else:
                target_url = st.text_input("目标地址", value=default_url, disabled=True, key="capture_fixed_url")
        with action_col:
            start_capture = st.button("开始抓取", icon=":material/search:", type="primary", width="stretch",
                                      disabled=not target_url or capture_running or download_running,
                                      key="capture_start_btn")
        with stop_col:
            stop_capture = st.button("停止抓取", icon=":material/stop_circle:", width="stretch",
                                     disabled=not capture_running or capture_stopping, key="capture_stop_btn")
            if stop_capture:
                try:
                    backend_client().stop_capture()
                    set_notice("warning", "正在停止抓取并提取已加载数据...")
                except BackendClientError as exc:
                    set_notice("error", str(exc))
                st.rerun()

    if start_capture:
        try:
            result = backend_client().start_capture(target_url, mode)
        except BackendClientError as exc:
            set_notice("error", f"无法启动抓取：{exc}")
        else:
            if result.get("started"):
                st.rerun()
            set_notice("error", "已有抓取任务正在运行。")
        st.rerun()

    if capture and capture.get("status") != "idle":
        if capture["status"] in ("success", "failed"):
            if capture["error"]:
                set_notice("error", f"抓取出错：{capture['error']}")
            else:
                try:
                    results = backend_client().capture_results()
                except BackendClientError as exc:
                    set_notice("error", f"读取抓取结果失败：{exc}")
                    st.rerun()
                items = results.get("items", [])
                if items:
                    st.session_state.collected_ids = [
                        {"视频ID": item["video_id"], "标题": item["title"]}
                        for item in items
                    ]
                    set_notice("success", f"提取完成，共获得 {len(items)} 个视频。")
                else:
                    set_notice("warning", "抓取结束，未获得有效视频。")
            try:
                backend_client().clear_capture()
            except BackendClientError as exc:
                logger.warning("清理抓取状态失败：%s", exc)
            st.rerun()

        stop_requested_at = capture["stop_requested_at"]
        with st.status("正在提取抓取结果..." if stop_requested_at else "正在动态抓取中...", expanded=True) as status:
            status.write(capture["message"] or "正在启动浏览器...")
        time.sleep(CAPTURE_UI_POLL_SECONDS)
        st.rerun()

    frame = pd.DataFrame(st.session_state.collected_ids)
    if frame.empty:
        summary = video_summary_data()
        metric_cols = st.columns(3)
        metric_cols[0].metric("视频库", summary.get("total", 0), border=True)
        metric_cols[1].metric("未下载", summary.get("pending_total", 0), border=True)
        metric_cols[2].metric("队列待处理", summary.get("waiting_tasks", 0), border=True)
        try:
            recent_result = backend_client().get_videos(
                page=1,
                page_size=20,
                sort_by="library",
                sort_order="desc",
            )
        except BackendClientError as exc:
            st.error(f"读取最近视频失败：{exc}")
            return
        recent_view = video_items_frame(recent_result.get("items", []))
        if recent_view.empty:
            st.caption("暂无抓取结果，开始抓取后会在这里显示视频列表。")
            return
        st.caption("最近保存的视频")
        st.dataframe(recent_view, hide_index=True, width="stretch", height=330, row_height=34,
                     column_config={"视频ID": st.column_config.TextColumn("视频ID", width="medium"),
                                    "标题": st.column_config.TextColumn("标题", width="large"),
                                    "状态": st.column_config.TextColumn("状态", width="small"),
                                    "操作": st.column_config.TextColumn("操作", width="small")},
                     key="capture_recent_table")
        if st.button("载入最近 50 条到工作区", icon=":material/library_add:", key="capture_load_recent_btn"):
            try:
                loaded_result = backend_client().get_videos(
                    page=1,
                    page_size=50,
                    sort_by="library",
                    sort_order="desc",
                )
            except BackendClientError as exc:
                set_notice("error", f"载入视频失败：{exc}")
                st.rerun()
            st.session_state.collected_ids = [
                {"视频ID": item["video_id"], "标题": item["title"]}
                for item in loaded_result.get("items", [])
            ]
            st.rerun()
        return

    if "视频ID" not in frame.columns: frame["视频ID"] = ""
    if "标题" not in frame.columns: frame["标题"] = "无标题"
    display_frame = preview_frame(frame[["视频ID", "标题"]], downloaded_ids)

    filter_col, search_col = st.columns([1.7, 3.0], vertical_alignment="bottom")
    with filter_col:
        status_filter = st.segmented_control("状态筛选", ["全部", "待下载", "已下载", "队列中", "下载失败"],
                                             default="全部", key="capture_status_filter", width="stretch") or "全部"
    with search_col:
        search_text = st.text_input("搜索抓取结果", placeholder="输入视频 ID 或标题", key="capture_search_text").strip()

    filtered = display_frame.copy()
    if status_filter != "全部": filtered = filtered[filtered["状态"] == status_filter]
    if search_text: filtered = filtered[
        filtered["视频ID"].str.contains(search_text, case=False, na=False) | filtered["标题"].str.contains(search_text,
                                                                                                           case=False,
                                                                                                           na=False)]

    st.caption(f"显示 {len(filtered)} / {len(display_frame)} 条")
    filtered = filtered.reset_index(drop=True)

    table_state = st.dataframe(filtered, hide_index=True, width="stretch", height=390, row_height=34, on_select="rerun",
                               selection_mode="multi-row", key="capture_main_table",
                               column_config={"视频ID": st.column_config.TextColumn("视频ID", width="medium"),
                                              "标题": st.column_config.TextColumn("标题", width="large"),
                                              "状态": st.column_config.TextColumn("状态", width="small"),
                                              "操作": st.column_config.TextColumn("操作", width="small")})

    selected_indices = selected_rows_from_state(table_state)
    if selected_indices:
        valid_indices = [i for i in selected_indices if i < len(filtered)]
        if valid_indices:
            selected = filtered.iloc[valid_indices]
            records = [{"id": row["视频ID"], "title": row["标题"]} for _, row in selected.iterrows()]
            render_selection_bar("capture_action_bar", len(records), "capture_main_table", records)


def render_download_tab(downloaded_ids):
    download_running = bool(download_queue_status_data().get("running"))
    capture_running = bool(capture_status_data().get("status") == "running")
    with st.container(key="download_tools"):
        input_col, force_col, action_col = st.columns([4.0, 1.1, 1.15], vertical_alignment="bottom")
        with input_col: raw_video_id = st.text_input("视频 ID 或链接", key="download_id_input",
                                                     placeholder="粘贴 19 位视频 ID，或包含 ID 的抖音链接").strip()
        video_id_match = AWEME_ID_RE.search(raw_video_id)
        video_id = video_id_match.group(1) if video_id_match else ""
        with force_col: force_download = st.checkbox("强制重新下载", key="force_download",
                                                     help="视频已下载时仍重新抓取并覆盖本地文件，同时更新统计记录。")
        with action_col: download_clicked = st.button("立即下载", icon=":material/download:", type="primary",
                                                      width="stretch",
                                                      disabled=not video_id or download_running or capture_running,
                                                      key="download_single_btn")

    if raw_video_id and not video_id:
        st.error("请输入 19 位纯数字视频 ID，或粘贴包含视频 ID 的链接。")
    elif video_id and video_id in downloaded_ids and not force_download:
        st.warning("该视频已有下载记录。如需覆盖文件，请勾选“强制重新下载”。")
    elif video_id:
        st.caption("视频可以立即下载，也可以从抓取结果或视频库加入队列。")

    if download_clicked and video_id:
        queue_immediate_download(video_id, force=force_download)
        st.rerun()


def render_library_tab(analytics):
    page_size = 50
    st.session_state.setdefault("library_page", 1)
    with st.container(key="library_tools"):
        filter_col, search_col = st.columns([1.7, 3.0], vertical_alignment="bottom")
        with filter_col: status_filter = st.segmented_control("状态筛选",
                                                              ["全部", "待下载", "已下载", "队列中", "下载失败"],
                                                              default="全部", key="library_status_filter",
                                                              width="stretch") or "全部"
        with search_col: search_text = st.text_input("搜索视频库", placeholder="输入视频 ID 或标题",
                                                     key="library_search_text").strip()
        queue_action_slot = st.empty()

    query_signature = (status_filter, search_text)
    if st.session_state.get("library_query_signature") != query_signature:
        st.session_state.library_query_signature = query_signature
        st.session_state.library_page = 1
        reset_library_table_selection()

    try:
        response = backend_client().get_videos(
            keyword=search_text,
            status=STATUS_FILTER_MAP.get(status_filter, "all"),
            page=st.session_state.library_page,
            page_size=page_size,
            sort_by="library",
            sort_order="asc",
        )
    except BackendClientError as exc:
        st.error(f"读取视频库失败：{exc}")
        return

    total = int(response.get("total", 0))
    total_pages = int(response.get("total_pages", 0))
    if total_pages and st.session_state.library_page > total_pages:
        st.session_state.library_page = total_pages
        reset_library_table_selection()
        st.rerun()

    if total == 0 and not search_text and status_filter == "全部":
        st.warning("视频库为空，请先在“抓取”页面保存视频。")

    filtered = video_items_frame(response.get("items", []))
    st.caption(f"显示 {len(filtered)} / {total} 条")
    library_table_key = f"library_main_table_{st.session_state.library_table_epoch}"

    table_state = st.dataframe(filtered, hide_index=True, width="stretch", height=410, row_height=34, on_select="rerun",
                               selection_mode="multi-row", key=library_table_key,
                               column_config={"视频ID": st.column_config.TextColumn("视频ID", width="medium"),
                                              "标题": st.column_config.TextColumn("标题", width="large"),
                                              "状态": st.column_config.TextColumn("状态", width="small"),
                                              "操作": st.column_config.TextColumn("操作", width="small")})

    selected_indices = selected_rows_from_state(table_state)
    records = []
    if selected_indices:
        valid_indices = [i for i in selected_indices if i < len(filtered)]
        if valid_indices:
            selected = filtered.iloc[valid_indices]
            records = [{"id": row["视频ID"], "title": row["标题"]} for _, row in selected.iterrows()]
    render_library_queue_action(queue_action_slot, library_table_key, records)

    page_col, prev_col, next_col = st.columns([5.2, 1.0, 1.0], vertical_alignment="center")
    with page_col:
        st.caption(f"第 {st.session_state.library_page} / {max(total_pages, 1)} 页 · 共 {total} 条")
    with prev_col:
        previous_clicked = st.button(
            "上一页",
            width="stretch",
            disabled=st.session_state.library_page <= 1,
            key="library_prev_page",
        )
    with next_col:
        next_clicked = st.button(
            "下一页",
            width="stretch",
            disabled=total_pages == 0 or st.session_state.library_page >= total_pages,
            key="library_next_page",
        )
    if previous_clicked:
        st.session_state.library_page = max(1, st.session_state.library_page - 1)
        reset_library_table_selection()
        st.rerun()
    if next_clicked:
        st.session_state.library_page = min(total_pages, st.session_state.library_page + 1)
        reset_library_table_selection()
        st.rerun()
    render_analytics_section(analytics)


def render_analytics_section(analytics):
    with st.expander(f"已下载数据 · {len(analytics)} 条", expanded=False):
        if analytics.empty: st.caption("暂无下载记录。"); return
        search_text = st.text_input("搜索已下载数据", placeholder="输入视频 ID、标题或作者",
                                    key="analytics_search_text").strip()
        if search_text:
            try:
                result = backend_client().get_analytics(keyword=search_text)
                filtered = analytics_items_frame(result.get("items", []))
            except BackendClientError as exc:
                st.error(f"搜索统计数据失败：{exc}")
                return
        else:
            filtered = analytics.copy()
        st.caption(f"显示 {len(filtered)} / {len(analytics)} 条")
        st.dataframe(filtered, hide_index=True, width="stretch", height=330, row_height=32, key="analytics_data_table")


# ================= 主界面 =================
try:
    auth_status = backend_client().get_auth_status()
    summary = backend_client().get_video_summary()
    analytics_result = backend_client().get_analytics()
    capture_status = backend_client().capture_status()
    downloads_snapshot = backend_client().downloads_snapshot()
except BackendClientError as exc:
    st.session_state.pop("auth_status", None)
    st.session_state.pop("video_summary", None)
    st.session_state.pop("capture_status", None)
    st.session_state.pop("downloads_snapshot", None)
    st.error(f"Backend 服务不可用：{exc}")
    st.stop()

st.session_state.auth_status = auth_status
st.session_state.video_summary = summary
st.session_state.capture_status = capture_status
st.session_state.downloads_snapshot = downloads_snapshot
analytics_df = analytics_items_frame(analytics_result.get("items", []))
downloaded_ids = {item.get("video_id", "") for item in analytics_result.get("items", [])}

render_toolbar(
    summary.get("total", 0),
    summary.get("downloaded_total", 0),
    auth_status,
    capture_status,
)
render_notice()

if st.session_state.pop("download_start_requested", False):
    if not start_download_queue_worker():
        set_notice("warning", "下载队列已经在运行。")
    st.rerun()

if bool(download_queue_status_data().get("running")):
    render_download_queue_progress()
else:
    workspace_section = st.segmented_control(
        "工作区",
        ["抓取", "下载", "视频库"],
        key="workspace_section",
        label_visibility="collapsed",
        width="stretch",
    ) or "抓取"
    if workspace_section == "抓取":
        render_capture_tab(downloaded_ids, capture_status)
    elif workspace_section == "下载":
        render_download_tab(downloaded_ids)
    else:
        render_library_tab(analytics_df)

render_download_dock()
