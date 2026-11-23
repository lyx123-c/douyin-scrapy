import json
import logging
import os
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from contextlib import contextmanager
from typing import Any, Optional

from config import DOWNLOAD_QUEUE_JSON, SAVE_DIR


VALID_TASK_STATUSES = {
    "waiting",
    "running",
    "success",
    "failed",
    "skipped",
    "interrupted",
}

_BROWSER_PROFILE_LOCK = threading.Lock()


@contextmanager
def browser_profile_guard(owner, timeout=5):
    acquired = _BROWSER_PROFILE_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise RuntimeError(
            f"Scrapling 浏览器 Profile 正被占用，暂时无法启动：{owner}"
        )
    try:
        yield
    finally:
        _BROWSER_PROFILE_LOCK.release()


logger = logging.getLogger(__name__)


class TaskManager:
    """
    任务管理器。

    特点：
    1. 任务状态持久化到 JSON
    2. 程序重启时 running -> interrupted
    3. 状态变化立即落盘
    4. progress 高频更新进行落盘节流
    5. JSON 原子写入
    6. 不依赖 Streamlit，可被 Streamlit / FastAPI 共用
    """

    # progress 最短落盘间隔
    PROGRESS_SAVE_INTERVAL = 1.5

    def __init__(self, store_path=None):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._store_path = str(store_path or DOWNLOAD_QUEUE_JSON)

        # TaskManager 自己的锁
        self._lock = threading.RLock()

        # 上一次持久化时间
        self._last_dump_time = 0.0

        # 防止重复 dump
        self._dump_lock = threading.Lock()
        self._download_worker_running = False
        self._progress_callback = None
        self._capture = None

        os.makedirs(
            os.path.dirname(self._store_path) or ".",
            exist_ok=True,
        )

        self._load()

    # ============================================================
    # 基础工具
    # ============================================================

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # ============================================================
    # 持久化
    # ============================================================

    # 调用方不得持有 _lock，统一锁顺序：_dump_lock -> _lock。
    def _dump(self, force: bool = False) -> bool:
        """
        将任务状态原子写入 JSON。

        force=True：
            状态发生关键变化时立即保存。

        force=False：
            主要用于 progress 更新，进行节流。

        返回 True 仅表示本次调用已经完成 fsync 和原子替换。
        节流跳过或持久化失败均返回 False。
        """

        now = time.monotonic()

        if not force:
            if now - self._last_dump_time < self.PROGRESS_SAVE_INTERVAL:
                return False

        try:
            # 防止多个线程同时写 JSON
            with self._dump_lock:
                now = time.monotonic()

                if not force:
                    if now - self._last_dump_time < self.PROGRESS_SAVE_INTERVAL:
                        return False

                with self._lock:
                    data = {
                        "version": 1,
                        "updated_at": self._now(),
                        "tasks": deepcopy(list(self._tasks.values())),
                    }

                directory = os.path.dirname(self._store_path) or "."
                os.makedirs(directory, exist_ok=True)

                fd = None
                tmp_path = None

                try:
                    fd, tmp_path = tempfile.mkstemp(
                        dir=directory,
                        prefix=".download_queue_",
                        suffix=".tmp",
                    )

                    with os.fdopen(
                        fd,
                        "w",
                        encoding="utf-8",
                    ) as f:
                        fd = None

                        json.dump(
                            data,
                            f,
                            ensure_ascii=False,
                            indent=2,
                        )

                        # 确保 Python 层面的缓冲区已经写入
                        f.flush()
                        os.fsync(f.fileno())

                    # 原子替换
                    os.replace(
                        tmp_path,
                        self._store_path,
                    )

                    tmp_path = None
                    self._last_dump_time = time.monotonic()
                    return True

                finally:
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass

                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
        except Exception:
            logger.exception(
                "任务队列持久化失败: %s",
                self._store_path,
            )
            return False

    # ============================================================
    # 加载
    # ============================================================

    def _load(self) -> None:
        """
        程序启动时恢复任务。

        running 不直接变成 waiting，
        而是变成 interrupted。

        这样可以区分：

        waiting
            从未执行 / 等待执行

        interrupted
            上次执行过程中程序退出
        """

        if not os.path.exists(self._store_path):
            return

        try:
            with open(
                self._store_path,
                "r",
                encoding="utf-8",
            ) as f:
                data = json.load(f)

            if not isinstance(data, dict):
                raise ValueError("任务存档格式错误：根节点不是 object")

            tasks = data.get("tasks", [])

            if not isinstance(tasks, list):
                raise ValueError("任务存档格式错误：tasks 不是 list")

            recovered = 0
            normalized = 0

            with self._lock:
                for task in tasks:
                    if not isinstance(task, dict):
                        continue

                    task_id = task.get("task_id")

                    if not task_id:
                        logger.warning(
                            "跳过没有 task_id 的任务: %r",
                            task,
                        )
                        continue

                    task.setdefault("task_type", "download" if task.get("video_id") else "unknown")
                    if task.get("task_type") == "download" and not task.get("target_dir"):
                        task["target_dir"] = SAVE_DIR
                        normalized += 1
                    task.setdefault("status", "waiting")
                    task.setdefault("progress", 0.0)
                    task.setdefault("message", "")
                    task.setdefault("error", None)
                    task.setdefault("retry_count", 0)
                    task.setdefault("created_at", self._now())
                    task.setdefault("updated_at", self._now())

                    # 核心恢复逻辑：
                    # 上次程序运行时正在执行，
                    # 这次启动时不能假装仍然 running。
                    if task.get("status") == "running":
                        task["status"] = "interrupted"
                        task["message"] = (
                            "任务因程序退出而中断，可点击重试"
                        )
                        task["updated_at"] = self._now()

                        recovered += 1

                    self._tasks[task_id] = task

            logger.info(
                "任务队列恢复完成：%d 个任务，%d 个 running 任务被标记为 interrupted",
                len(self._tasks),
                recovered,
            )

            if recovered or normalized:
                self._dump(force=True)

        except json.JSONDecodeError:
            logger.exception(
                "任务队列 JSON 损坏：%s",
                self._store_path,
            )

        except Exception:
            logger.exception(
                "加载任务队列失败：%s",
                self._store_path,
            )

    # ============================================================
    # 查询
    # ============================================================

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)

            if task is None:
                return None

            return deepcopy(task)

    def get_tasks(self) -> list[dict]:
        with self._lock:
            return deepcopy(list(self._tasks.values()))

    def counts(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {status: 0 for status in VALID_TASK_STATUSES}

            for task in self._tasks.values():
                status = task.get("status", "unknown")
                result[status] = result.get(status, 0) + 1

            return result

    def is_download_running(self) -> bool:
        with self._lock:
            return self._download_worker_running or any(
                task.get("status") == "running"
                and task.get("task_type") == "download"
                for task in self._tasks.values()
            )

    def start_download_queue(self) -> bool:
        with self._lock:
            if self._download_worker_running:
                return False
            self._download_worker_running = True
            return True

    def finish_download_queue(self) -> None:
        with self._lock:
            self._download_worker_running = False

    # ============================================================
    # 创建任务
    # ============================================================

    def add_task(
        self,
        task_id: str,
        task_type: str,
        *,
        video_id: Optional[str] = None,
        title: Optional[str] = None,
        **extra,
    ) -> dict:

        now = self._now()

        task = {
            "task_id": task_id,
            "task_type": task_type,
            "video_id": video_id,
            "title": title,

            "status": "waiting",
            "progress": 0.0,
            "message": "等待执行",

            "retry_count": 0,

            "created_at": now,
            "updated_at": now,

            "error": None,
        }

        task.update(extra)

        if task.get("status") not in VALID_TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {task.get('status')}")

        with self._lock:
            self._tasks[task_id] = task

        # 新任务必须立即落盘
        self._dump(force=True)

        return deepcopy(task)

    # ============================================================
    # 更新任务
    # ============================================================

    def update_task(
        self,
        task_id: str,
        *,
        persist: bool = True,
        **fields,
    ) -> Optional[dict]:

        if "status" in fields and fields["status"] not in VALID_TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {fields['status']}")

        with self._lock:
            task = self._tasks.get(task_id)

            if task is None:
                logger.warning(
                    "尝试更新不存在的任务: %s",
                    task_id,
                )
                return None

            old_status = task.get("status")

            task.update(fields)
            task["updated_at"] = self._now()

            new_status = task.get("status")

            # 关键状态变化：
            # 立即落盘
            status_changed = old_status != new_status

            # progress 更新：
            # 走节流
            progress_changed = "progress" in fields

            result = deepcopy(task)

        if persist:
            if status_changed:
                self._dump(force=True)

            elif progress_changed:
                self._dump(force=False)

            else:
                # 其他重要字段变化也立即保存
                self._dump(force=True)

        return result

    # ============================================================
    # 开始任务
    # ============================================================

    def mark_running(
        self,
        task_id: str,
        message: str = "正在执行",
    ):
        return self.update_task(
            task_id,
            status="running",
            message=message,
            error=None,
        )

    # ============================================================
    # 成功
    # ============================================================

    def mark_success(
        self,
        task_id: str,
        message: str = "任务完成",
    ):
        return self.update_task(
            task_id,
            status="success",
            progress=1.0,
            message=message,
            error=None,
        )

    # ============================================================
    # 失败
    # ============================================================

    def mark_failed(
        self,
        task_id: str,
        error: str,
        message: str = "任务失败",
    ):
        return self.update_task(
            task_id,
            status="failed",
            message=message,
            error=error,
        )

    # ============================================================
    # 跳过
    # ============================================================

    def mark_skipped(
        self,
        task_id: str,
        message: str = "任务已跳过",
    ):
        return self.update_task(
            task_id,
            status="skipped",
            message=message,
        )

    # ============================================================
    # 重试
    # ============================================================

    def retry_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)

            if task is None:
                return None

            status = task.get("status")

            if status not in {
                "failed",
                "interrupted",
            }:
                raise ValueError(
                    f"当前状态不可重试: {status}"
                )

            task["status"] = "waiting"
            task["progress"] = 0.0
            task["message"] = "等待重新执行"
            task["error"] = None
            task["retry_count"] = (
                int(task.get("retry_count", 0)) + 1
            )
            task["updated_at"] = self._now()

            result = deepcopy(task)

        self._dump(force=True)

        return result

    # ============================================================
    # 删除
    # ============================================================

    def remove_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.get("status") == "running":
                return False

            del self._tasks[task_id]

        self._dump(force=True)

        return True

    # ============================================================
    # 恢复 interrupted
    # ============================================================

    def get_interrupted_tasks(self) -> list[dict]:
        with self._lock:
            return deepcopy([
                task
                for task in self._tasks.values()
                if task.get("status") == "interrupted"
            ])

    # ============================================================
    # 获取等待任务
    # ============================================================

    def get_waiting_tasks(self) -> list[dict]:
        with self._lock:
            return deepcopy([
                task
                for task in self._tasks.values()
                if task.get("status") == "waiting"
            ])

    # ============================================================
    # 手动强制保存
    # ============================================================

    def save(self) -> bool:
        return self._dump(force=True)

    def reload_from_disk(self) -> bool:
        """Reload persisted tasks without disturbing a running download thread."""
        if self.is_download_running():
            return False

        fresh = TaskManager(self._store_path)

        with self._lock:
            if self.is_download_running():
                return False
            self._tasks = deepcopy(fresh._tasks)
            self._last_dump_time = fresh._last_dump_time

        return True

    def enqueue_download(self, video_id, title, save_dir=None):
        video_id = str(video_id).strip()
        with self._lock:
            for task in self._tasks.values():
                if (
                    task.get("task_type") == "download"
                    and str(task.get("video_id", "")) == video_id
                ):
                    return deepcopy(task), False

            now = self._now()
            task = {
                "task_id": uuid.uuid4().hex,
                "task_type": "download",
                "video_id": video_id,
                "title": title,
                "target_dir": str(save_dir or SAVE_DIR),
                "status": "waiting",
                "progress": 0.0,
                "message": "等待执行",
                "retry_count": 0,
                "created_at": now,
                "updated_at": now,
                "error": None,
            }
            self._tasks[task["task_id"]] = task

        self._dump(force=True)
        return deepcopy(task), True

    def find_by_video_id(self, video_id):
        video_id = str(video_id).strip()
        with self._lock:
            for task in reversed(list(self._tasks.values())):
                if (
                    task.get("task_type") == "download"
                    and str(task.get("video_id", "")) == video_id
                ):
                    return deepcopy(task)
        return None

    def next_waiting_download(self):
        with self._lock:
            task = None
            for candidate in self._tasks.values():
                if (
                    candidate.get("task_type") == "download"
                    and candidate.get("status") == "waiting"
                ):
                    candidate.update(
                        {
                            "status": "running",
                            "message": "正在执行",
                            "error": None,
                            "updated_at": self._now(),
                        }
                    )
                    task = deepcopy(candidate)
                    break

        if task is None:
            return None

        self._dump(force=True)
        return task

    def remove_waiting_task(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.get("status") != "waiting":
                return False
            del self._tasks[task_id]

        self._dump(force=True)
        return True

    def clear_completed(self):
        with self._lock:
            remove_ids = [
                task_id
                for task_id, task in self._tasks.items()
                if task.get("status") in ("success", "skipped")
            ]
            for task_id in remove_ids:
                del self._tasks[task_id]
        if remove_ids:
            self._dump(force=True)
        return len(remove_ids)

    def report_progress(self, task_id, payload):
        payload = dict(payload or {})
        fields = {
            "message": payload.get("message") or "正在执行",
            "progress_details": deepcopy(payload),
        }
        if payload.get("progress") is not None:
            fields["progress"] = float(payload["progress"])
        self.update_task(task_id, **fields)

    def start_capture(self):
        with self._lock:
            if self._capture and self._capture.get("status") == "running":
                return None
            task_id = uuid.uuid4().hex
            self._capture = {
                "task_id": task_id,
                "status": "running",
                "message": "正在启动浏览器...",
                "progress": {"stage": "starting", "progress": 0.0},
                "results": [],
                "error": None,
                "stop_event": threading.Event(),
                "thread": None,
                "stopping": False,
                "stop_requested_at": None,
                "kill_requested": False,
            }
            return task_id

    def set_capture_thread(self, thread):
        with self._lock:
            if self._capture:
                self._capture["thread"] = thread

    def get_capture_snapshot(self):
        with self._lock:
            if not self._capture:
                return None
            capture = self._capture
            return {
                "task_id": capture["task_id"],
                "status": capture["status"],
                "message": capture["message"],
                "progress": deepcopy(capture["progress"]),
                "results": deepcopy(capture["results"]),
                "error": capture["error"],
                "stopping": capture["stopping"],
                "stop_requested_at": capture["stop_requested_at"],
            }

    def is_capture_running(self):
        with self._lock:
            return bool(self._capture and self._capture.get("status") == "running")

    def request_capture_stop(self):
        with self._lock:
            if not self._capture or self._capture.get("status") != "running":
                return False
            if self._capture["stop_event"].is_set():
                return False
            self._capture["stop_event"].set()
            self._capture["stopping"] = True
            self._capture["stop_requested_at"] = time.monotonic()
            self._capture["message"] = "正在停止抓取并提取已加载数据..."
            return True

    def capture_stop_requested(self):
        with self._lock:
            return bool(self._capture and self._capture["stop_event"].is_set())

    def update_capture_progress(self, payload):
        with self._lock:
            if not self._capture:
                return
            if isinstance(payload, dict):
                self._capture["progress"] = deepcopy(payload)
                self._capture["message"] = payload.get("message") or self._capture["message"]
            else:
                self._capture["message"] = str(payload)

    def finish_capture(self, results=None, error=None):
        with self._lock:
            if not self._capture:
                return
            self._capture["results"] = list(results or [])
            self._capture["error"] = error
            self._capture["status"] = "failed" if error else "success"
            self._capture["message"] = error or f"提取完成，共获得 {len(results or [])} 个视频。"

    def mark_capture_kill_requested(self):
        with self._lock:
            if not self._capture or self._capture["kill_requested"]:
                return False
            self._capture["kill_requested"] = True
            return True

    def clear_capture(self):
        with self._lock:
            self._capture = None
