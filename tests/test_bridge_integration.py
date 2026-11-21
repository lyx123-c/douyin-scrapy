import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
import pandas as pd
import streamlit as st
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from backend.api.main import app
from backend.api.ownership import RuntimeOwnershipError, acquire_backend_runtime_ownership
from backend.services.capture_service import CaptureService
from backend.services.download_queue_service import DownloadQueueService
from backend.services.runtime import RuntimeServices
from backend.services.task_manager import TaskManager
from backend.services.video_query_service import VideoQueryService
from backend_client.client import (
    BackendClient,
    BackendHTTPError,
    BackendResponseError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from config import BACKEND_API_LOGIN_TIMEOUT


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app1.py"


@contextmanager
def fake_ownership(*args, **kwargs):
    yield "test-owner"


def base_auth_status():
    return {"login_ready": True}


def base_downloads():
    return {
        "items": [],
        "total": 0,
        "counts": {
            "waiting": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "interrupted": 0,
        },
        "queue": {
            "running": False,
            "thread_alive": False,
            "stopping": False,
            "forced_task_count": 0,
            "counts": {},
        },
    }


class FakeBackendClient:
    def __init__(self, auth_status=None):
        self.auth_status_data = auth_status or base_auth_status()
        self.login_error = None
        self.downloads_data = base_downloads()
        self.calls = []
        self.videos = []
        self.analytics_items = []
        self.capture_data = {
            "task_id": None,
            "status": "idle",
            "message": "",
            "progress": {},
            "error": None,
            "stopping": False,
            "stop_requested_at": None,
        }
        self.capture_results_data = {"task_id": None, "items": [], "total": 0}

    def get_auth_status(self):
        self.calls.append(("get_auth_status",))
        return json.loads(json.dumps(self.auth_status_data))

    def get_video_summary(self):
        self.calls.append(("get_video_summary",))
        return {
            "total": len(self.videos),
            "downloaded_total": len(self.analytics_items),
            "pending_total": max(len(self.videos) - len(self.analytics_items), 0),
            "waiting_tasks": self.downloads_data["counts"].get("waiting", 0),
            "running_tasks": self.downloads_data["counts"].get("running", 0),
            "failed_tasks": self.downloads_data["counts"].get("failed", 0),
        }

    def get_videos(self, *, keyword="", status="all", page=1, page_size=50, sort_by="library", sort_order="asc"):
        self.calls.append(
            ("get_videos", keyword, status, page, page_size, sort_by, sort_order)
        )
        items = list(self.videos)
        if keyword:
            items = [
                item
                for item in items
                if keyword.lower() in item["video_id"].lower()
                or keyword.lower() in item["title"].lower()
            ]
        if status != "all":
            items = [item for item in items if item["status"] == status]
        if sort_order == "desc":
            items = list(reversed(items))
        total = len(items)
        total_pages = (total + page_size - 1) // page_size if total else 0
        start = (page - 1) * page_size
        return {
            "items": items[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    def get_analytics(self, keyword=""):
        self.calls.append(("get_analytics", keyword))
        items = list(self.analytics_items)
        if keyword:
            items = [
                item
                for item in items
                if keyword.lower() in item["video_id"].lower()
                or keyword.lower() in item["video_title"].lower()
                or keyword.lower() in item["author_name"].lower()
            ]
        return {"items": items, "total": len(items)}

    def capture_status(self):
        self.calls.append(("capture_status",))
        return json.loads(json.dumps(self.capture_data))

    def capture_results(self):
        self.calls.append(("capture_results",))
        return json.loads(json.dumps(self.capture_results_data))

    def start_capture(self, target_url, mode):
        self.calls.append(("start_capture", target_url, mode))
        return {"started": True, "task_id": "capture-test"}

    def stop_capture(self):
        self.calls.append(("stop_capture",))
        return {"stopping": True}

    def clear_capture(self):
        self.calls.append(("clear_capture",))
        return {"cleared": True}

    def start_downloads(self):
        self.calls.append(("start_downloads",))
        return {"started": True, "running": True}

    def stop_downloads(self):
        self.calls.append(("stop_downloads",))
        return {"stopping": True}

    def downloads_snapshot(self):
        self.calls.append(("downloads_snapshot",))
        return json.loads(json.dumps(self.downloads_data))

    def queue_items(self, items):
        self.calls.append(("queue_items", items))
        return {"created_count": len(items), "created": []}

    def immediate_download(self, video_id, title, force=False):
        self.calls.append(("immediate_download", video_id, title, force))
        return {"queued": True, "reason": "queued", "task": {"task_id": "task-test"}}

    def retry_task(self, task_id):
        self.calls.append(("retry_task", task_id))
        return {"task_id": task_id, "status": "waiting"}

    def delete_task(self, task_id):
        self.calls.append(("delete_task", task_id))
        return {"removed": True}

    def clear_completed(self):
        self.calls.append(("clear_completed",))
        return {"removed": 0}

    def login(self):
        self.calls.append(("login",))
        if self.login_error is not None:
            raise self.login_error
        self.auth_status_data["login_ready"] = True
        return {"profile_saved": True}

    def open_save_directory(self):
        self.calls.append(("open_save_directory",))
        return {"path": "mock-save-dir"}


class UnavailableBackendClient(FakeBackendClient):
    def get_auth_status(self):
        raise BackendUnavailableError("Backend 不可用")


class FastAPIIntegrationTests(unittest.TestCase):
    def test_lifespan_creates_single_runtime_and_auth_status(self):
        counter = {"count": 0}
        original_runtime = RuntimeServices

        def runtime_factory(*args, **kwargs):
            counter["count"] += 1
            return original_runtime(*args, **kwargs)

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.main.RuntimeServices", side_effect=runtime_factory),
            patch("backend.api.routes.auth.login_profile_ready", return_value=True),
        ):
            with TestClient(app) as client:
                first_runtime = app.state.runtime
                self.assertIs(first_runtime.task_manager, app.state.task_manager)
                self.assertEqual(counter["count"], 1)

                health = client.get("/api/health")
                auth_status = client.get("/api/auth/status")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(auth_status.status_code, 200)
                self.assertEqual(auth_status.json()["data"], {"login_ready": True})
                self.assertIs(app.state.runtime, first_runtime)
                self.assertEqual(counter["count"], 1)

    def test_formal_capture_and_download_endpoints(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.capture.login_profile_state", return_value="ready"),
            patch.object(CaptureService, "start_capture", return_value="capture-test"),
            patch.object(CaptureService, "request_stop", return_value=True),
            patch.object(
                CaptureService,
                "get_public_status",
                return_value={
                    "task_id": "capture-test",
                    "status": "running",
                    "message": "抓取中",
                    "progress": {},
                    "error": None,
                    "stopping": False,
                    "stop_requested_at": None,
                },
            ),
            patch.object(
                CaptureService,
                "get_results",
                return_value={
                    "task_id": "capture-test",
                    "items": [{"video_id": "7700000000000000001", "title": "mock"}],
                    "total": 1,
                },
            ),
            patch.object(CaptureService, "clear_status"),
            patch.object(DownloadQueueService, "start", return_value=True),
            patch.object(DownloadQueueService, "is_running", return_value=True),
            patch.object(DownloadQueueService, "request_stop", return_value=True),
            patch.object(
                DownloadQueueService,
                "get_status",
                return_value={
                    "running": True,
                    "thread_alive": True,
                    "stopping": False,
                    "forced_task_count": 0,
                    "counts": {},
                },
            ),
        ):
            with TestClient(app) as client:
                started = client.post(
                    "/api/capture/start",
                    json={"target_url": "https://www.douyin.com/", "mode": "recommend"},
                )
                stopped = client.post("/api/capture/stop")
                status = client.get("/api/capture/status")
                results = client.get("/api/capture/results")
                cleared = client.post("/api/capture/clear")
                removed_bridge = client.get("/api/bridge/capture/status")
                download_started = client.post("/api/downloads/start")
                download_state = client.get("/api/downloads/status")
                downloads = client.get("/api/downloads")
                download_stopped = client.post("/api/downloads/stop")
                removed_bridge_download = client.post("/api/bridge/downloads/start")

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["data"]["task_id"], "capture-test")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["data"]["status"], "running")
        self.assertEqual(results.json()["data"]["total"], 1)
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(removed_bridge.status_code, 404)
        self.assertEqual(download_started.status_code, 200)
        self.assertTrue(download_started.json()["data"]["started"])
        self.assertTrue(download_state.json()["data"]["running"])
        self.assertIn("items", downloads.json()["data"])
        self.assertEqual(download_stopped.status_code, 200)
        self.assertEqual(removed_bridge_download.status_code, 404)

    def test_capture_duplicate_and_failure_http_errors(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.capture.login_profile_state", return_value="ready"),
            patch.object(
                CaptureService,
                "start_capture",
                side_effect=["capture-test", None],
            ),
        ):
            with TestClient(app) as client:
                first = client.post(
                    "/api/capture/start",
                    json={"target_url": "https://www.douyin.com/", "mode": "recommend"},
                )
                duplicate = client.post(
                    "/api/capture/start",
                    json={"target_url": "https://www.douyin.com/", "mode": "recommend"},
                )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["data"]["code"], "CAPTURE_BUSY")

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.capture.login_profile_state", return_value="ready"),
            patch.object(CaptureService, "start_capture", side_effect=RuntimeError("boom")),
        ):
            with TestClient(app) as client:
                failed = client.post(
                    "/api/capture/start",
                    json={"target_url": "https://www.douyin.com/", "mode": "recommend"},
                )
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["data"]["code"], "CAPTURE_START_FAILED")

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(CaptureService, "request_stop", side_effect=[True, False]),
        ):
            with TestClient(app) as client:
                stopped = client.post("/api/capture/stop")
                duplicate_stop = client.post("/api/capture/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(duplicate_stop.status_code, 409)
        self.assertEqual(duplicate_stop.json()["data"]["code"], "CAPTURE_NOT_RUNNING")

    def test_capture_start_requires_ready_login_before_service_call(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.capture.login_profile_state", return_value="missing"),
            patch.object(CaptureService, "start_capture") as start_mock,
            TestClient(app) as client,
        ):
            missing = client.post(
                "/api/capture/start",
                json={"target_url": "https://www.douyin.com/", "mode": "recommend"},
            )
        self.assertEqual(missing.status_code, 401)
        start_mock.assert_not_called()

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.capture.login_profile_state", return_value="unknown"),
            patch.object(CaptureService, "start_capture") as start_mock,
            TestClient(app) as client,
        ):
            unknown = client.post(
                "/api/capture/start",
                json={"target_url": "https://www.douyin.com/", "mode": "recommend"},
            )
        self.assertEqual(unknown.status_code, 503)
        self.assertEqual(unknown.json()["data"]["code"], "LOGIN_STATUS_UNKNOWN")
        start_mock.assert_not_called()

    def test_capture_start_keeps_four_modes_working_when_logged_in(self):
        modes = [
            ("recommend", "https://www.douyin.com/?recommend=1"),
            ("user", "https://www.douyin.com/user/mock"),
            ("jingxuan", "https://www.douyin.com/jingxuan"),
            ("search", "https://www.douyin.com/search/人工智能?type=video"),
        ]
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.capture.login_profile_state", return_value="ready"),
            patch.object(
                CaptureService,
                "start_capture",
                side_effect=["capture-1", "capture-2", "capture-3", "capture-4"],
            ) as start_mock,
            TestClient(app) as client,
        ):
            responses = [
                client.post(
                    "/api/capture/start",
                    json={"target_url": target_url, "mode": mode},
                )
                for mode, target_url in modes
            ]

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(start_mock.call_count, 4)

    def test_shutdown_calls_runtime_shutdown(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(RuntimeServices, "shutdown", return_value=True) as shutdown_mock,
        ):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(shutdown_mock.call_count, 1)

    def test_json_top_level_format_unchanged(self):
        queue_path = PROJECT_ROOT / "data" / "download_queue.json"
        before = json.loads(queue_path.read_text(encoding="utf-8"))
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
        ):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/auth/status").status_code, 200)
        after = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(before.keys(), after.keys())
        self.assertIsInstance(after.get("tasks"), list)

    def test_formal_download_task_and_system_actions(self):
        task = {
            "task_id": "task-1",
            "task_type": "download",
            "video_id": "7700000000000000001",
            "title": "mock",
            "status": "waiting",
            "progress": 0.0,
            "message": "等待执行",
            "retry_count": 0,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "error": None,
        }
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(
                DownloadQueueService,
                "queue_items",
                return_value={
                    "created": [task],
                    "existing": [],
                    "skipped": [],
                    "created_count": 1,
                },
            ),
            patch.object(
                DownloadQueueService,
                "queue_immediate",
                return_value={"queued": True, "reason": "queued", "task": task},
            ),
            patch.object(DownloadQueueService, "retry_task", return_value=task),
            patch.object(DownloadQueueService, "get_task", return_value=task),
            patch.object(
                DownloadQueueService,
                "remove_task",
                return_value={"removed": True, "reason": "removed"},
            ),
            patch.object(DownloadQueueService, "clear_completed", return_value=2),
            patch("backend.api.routes.auth.login_douyin", return_value="mock-profile"),
            patch("backend.api.routes.system.open_save_directory", return_value="mock-save"),
            TestClient(app) as client,
        ):
            queue = client.post(
                "/api/downloads/tasks",
                json={"items": [{"video_id": "7700000000000000001", "title": "mock"}]},
            )
            immediate = client.post(
                "/api/downloads/immediate",
                json={"video_id": "7700000000000000001", "title": "mock", "force": False},
            )
            retry = client.post("/api/downloads/tasks/task-1/retry")
            task_detail = client.get("/api/downloads/tasks/task-1")
            delete = client.delete("/api/downloads/tasks/task-1")
            clear = client.post("/api/downloads/tasks/clear-completed")
            removed_bridge = client.post(
                "/api/bridge/tasks/queue",
                json={"items": []},
            )
            login = client.post("/api/auth/login")
            folder = client.post("/api/system/open-save-directory")

        self.assertEqual(queue.status_code, 200)
        self.assertEqual(immediate.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(task_detail.status_code, 200)
        self.assertEqual(task_detail.json()["data"]["task_id"], "task-1")
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(clear.status_code, 200)
        self.assertEqual(removed_bridge.status_code, 404)
        self.assertEqual(login.status_code, 200)
        self.assertNotIn("profile_path", login.json()["data"])
        self.assertEqual(login.json()["data"], {"profile_saved": True})
        self.assertEqual(folder.json()["data"]["path"], "mock-save")

    def test_auth_status_reports_logged_out_state(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.auth.login_profile_ready", return_value=False),
            TestClient(app) as client,
        ):
            response = client.get("/api/auth/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"login_ready": False})

    def test_auth_login_uses_existing_runtime_task_manager(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch("backend.api.routes.auth.login_douyin", return_value="mock-profile") as login_mock,
            TestClient(app) as client,
        ):
            runtime = app.state.runtime
            task_manager = app.state.task_manager
            response = client.post("/api/auth/login")

            self.assertIs(app.state.runtime, runtime)
            self.assertIs(app.state.runtime.task_manager, task_manager)
            self.assertIs(app.state.task_manager, task_manager)
            login_mock.assert_called_once_with(task_manager)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"profile_saved": True})

    def test_auth_login_failure_returns_safe_business_error(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch(
                "backend.api.routes.auth.login_douyin",
                side_effect=RuntimeError("profile is busy"),
            ),
            TestClient(app) as client,
        ):
            response = client.post("/api/auth/login")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["data"]["code"], "LOGIN_FAILED")
        self.assertIn("登录失败", response.json()["message"])
        self.assertNotIn("Traceback", response.text)

    def test_system_open_directory_failure_returns_safe_error(self):
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch(
                "backend.api.routes.system.open_save_directory",
                side_effect=PermissionError("denied"),
            ),
            TestClient(app) as client,
        ):
            response = client.post("/api/system/open-save-directory")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["data"]["code"], "OPEN_DIRECTORY_FAILED")
        self.assertNotIn("Traceback", response.text)

    def test_bridge_routes_are_absent_from_http_and_openapi(self):
        bridge_routes = [
            ("GET", "/api/bridge/snapshot"),
            ("GET", "/api/bridge/services"),
            ("POST", "/api/bridge/login"),
            ("POST", "/api/bridge/open-save-directory"),
        ]
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            TestClient(app) as client,
        ):
            responses = [
                client.request(method, path)
                for method, path in bridge_routes
            ]
            openapi_paths = client.get("/openapi.json").json()["paths"]

        self.assertTrue(all(response.status_code == 404 for response in responses))
        self.assertFalse(any(path.startswith("/api/bridge") for path in openapi_paths))
        self.assertIn("/api/auth/status", openapi_paths)
        self.assertIn("/api/auth/login", openapi_paths)
        self.assertIn("/api/system/open-save-directory", openapi_paths)

    def test_download_duplicate_failure_and_delete_http_semantics(self):
        status = {
            "running": True,
            "thread_alive": True,
            "stopping": False,
            "forced_task_count": 0,
            "counts": {},
        }
        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(DownloadQueueService, "start", side_effect=[True, False]),
            patch.object(DownloadQueueService, "is_running", return_value=True),
            patch.object(DownloadQueueService, "get_status", return_value=status),
        ):
            with TestClient(app) as client:
                first_start = client.post("/api/downloads/start")
                duplicate_start = client.post("/api/downloads/start")
        self.assertEqual(first_start.status_code, 200)
        self.assertTrue(first_start.json()["data"]["started"])
        self.assertEqual(duplicate_start.status_code, 200)
        self.assertFalse(duplicate_start.json()["data"]["started"])

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(DownloadQueueService, "start", return_value=False),
            patch.object(DownloadQueueService, "is_running", return_value=False),
        ):
            with TestClient(app) as client:
                failed_start = client.post("/api/downloads/start")
        self.assertEqual(failed_start.status_code, 503)

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(DownloadQueueService, "request_stop", side_effect=[True, False, False]),
        ):
            with TestClient(app) as client:
                stopped = client.post("/api/downloads/stop")
                duplicate_stop = client.post("/api/downloads/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(duplicate_stop.status_code, 409)

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(DownloadQueueService, "get_task", return_value=None),
            patch.object(
                DownloadQueueService,
                "remove_task",
                return_value={"removed": False, "reason": "running"},
            ),
        ):
            with TestClient(app) as client:
                missing = client.get("/api/downloads/tasks/missing")
                running_delete = client.delete("/api/downloads/tasks/task-running")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(running_delete.status_code, 409)

    def test_formal_video_and_analytics_query_apis(self):
        library = pd.DataFrame(
            [
                {"视频ID": f"740000000000000{i:04d}", "标题": f"测试视频{i:02d}"}
                for i in range(12)
            ]
        )
        analytics = pd.DataFrame(
            [
                {
                    "视频ID": library.iloc[i]["视频ID"],
                    "作者昵称": f"作者{i}",
                    "视频标题": library.iloc[i]["标题"],
                    "点赞量": i,
                    "评论量": i,
                    "收藏量": i,
                    "转发量": i,
                }
                for i in range(3)
            ]
        )
        first = library.iloc[0]

        with (
            patch("backend.api.main.acquire_backend_runtime_ownership", fake_ownership),
            patch.object(TaskManager, "get_tasks", return_value=[]),
            patch(
                "backend.services.video_query_service.get_video_library_df",
                return_value=library,
            ),
            patch(
                "backend.services.video_query_service.get_analytics_df",
                return_value=analytics,
            ),
            patch(
                "backend.services.video_query_service.get_downloaded_ids",
                return_value={first["视频ID"]},
            ),
            patch.object(
                TaskManager,
                "counts",
                return_value={
                    "waiting": 0,
                    "running": 0,
                    "success": 0,
                    "failed": 0,
                    "skipped": 0,
                    "interrupted": 0,
                },
            ),
            TestClient(app) as client,
        ):
            page = client.get("/api/videos", params={"page": 1, "page_size": 10})
            search_id = client.get("/api/videos", params={"keyword": first["视频ID"]})
            search_title = client.get("/api/videos", params={"keyword": first["标题"][:8]})
            downloaded = client.get("/api/videos", params={"status": "downloaded", "page_size": 200})
            high_page = client.get("/api/videos", params={"page": 9999, "page_size": 50})
            bad_page = client.get("/api/videos", params={"page": 0})
            bad_size = client.get("/api/videos", params={"page_size": 201})
            sorted_desc = client.get(
                "/api/videos",
                params={"sort_by": "video_id", "sort_order": "desc", "page_size": 10},
            )
            summary = client.get("/api/videos/summary")
            analytics_response = client.get("/api/analytics")
            analytics_search = client.get("/api/analytics", params={"keyword": analytics.iloc[0]["视频ID"]})

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["data"]["total"], len(library))
        self.assertEqual(len(page.json()["data"]["items"]), min(10, len(library)))
        self.assertEqual(search_id.json()["data"]["items"][0]["video_id"], first["视频ID"])
        self.assertEqual(search_title.json()["data"]["items"][0]["video_id"], first["视频ID"])
        self.assertTrue(
            all(item["status"] == "downloaded" for item in downloaded.json()["data"]["items"])
        )
        self.assertEqual(high_page.status_code, 200)
        self.assertEqual(high_page.json()["data"]["items"], [])
        self.assertEqual(high_page.json()["data"]["page"], 9999)
        self.assertEqual(bad_page.status_code, 422)
        self.assertEqual(bad_size.status_code, 422)
        sorted_ids = [item["video_id"] for item in sorted_desc.json()["data"]["items"]]
        self.assertEqual(sorted_ids, sorted(sorted_ids, reverse=True))
        self.assertEqual(summary.json()["data"]["total"], len(library))
        self.assertEqual(analytics_response.json()["data"]["total"], len(analytics))
        self.assertEqual(len(analytics_search.json()["data"]["items"]), 1)


class VideoQueryServiceTests(unittest.TestCase):
    def test_empty_data_behavior(self):
        with tempfile.TemporaryDirectory(prefix="empty_video_query_") as temp_dir:
            manager = TaskManager(str(Path(temp_dir) / "download_queue.json"))
            service = VideoQueryService(RuntimeServices(task_manager=manager))
            empty_videos = pd.DataFrame(columns=["视频ID", "标题"])
            empty_analytics = pd.DataFrame(
                columns=["视频ID", "作者昵称", "视频标题", "点赞量", "评论量", "收藏量", "转发量"]
            )

            with (
                patch(
                    "backend.services.video_query_service.get_video_library_df",
                    return_value=empty_videos,
                ),
                patch(
                    "backend.services.video_query_service.get_analytics_df",
                    return_value=empty_analytics,
                ),
                patch(
                    "backend.services.video_query_service.get_downloaded_ids",
                    return_value=set(),
                ),
            ):
                page = service.list_videos(page=1, page_size=50)
                analytics = service.analytics()

        self.assertEqual(page["items"], [])
        self.assertEqual(page["total"], 0)
        self.assertEqual(page["page"], 1)
        self.assertEqual(page["total_pages"], 0)
        self.assertEqual(analytics, {"items": [], "total": 0})


class OwnershipTests(unittest.TestCase):
    def test_mutex_blocks_second_process_and_releases(self):
        mutex_name = f"Local\\CodexMutexTest-{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="mutex_test_") as temp_dir:
            ready = Path(temp_dir) / "ready.flag"
            done = Path(temp_dir) / "done.flag"
            script = (
                "import os, sys, time\n"
                "from backend.api.ownership import acquire_backend_runtime_ownership\n"
                "mutex, ready, done = sys.argv[1:4]\n"
                "with acquire_backend_runtime_ownership(mutex):\n"
                "    open(ready, 'w').close()\n"
                "    while not os.path.exists(done):\n"
                "        time.sleep(0.02)\n"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", script, mutex_name, str(ready), str(done)],
                cwd=PROJECT_ROOT,
            )
            try:
                deadline = time.time() + 10
                while not ready.exists() and time.time() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                with self.assertRaises(RuntimeOwnershipError):
                    with acquire_backend_runtime_ownership(mutex_name):
                        pass
            finally:
                done.touch()
                process.wait(timeout=10)

        with acquire_backend_runtime_ownership(mutex_name):
            pass


class BackendClientTests(unittest.TestCase):
    def make_client(self, handler):
        client = BackendClient("http://backend.test")
        client._client = httpx.Client(
            base_url="http://backend.test",
            transport=httpx.MockTransport(handler),
        )
        return client

    def test_unavailable_error(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        client = self.make_client(handler)
        with self.assertRaises(BackendUnavailableError):
            client.health()

    def test_timeout_error(self):
        def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)

        client = self.make_client(handler)
        with self.assertRaises(BackendTimeoutError):
            client.health()

    def test_http_error(self):
        def handler(request):
            return httpx.Response(
                409,
                json={
                    "success": False,
                    "data": {"code": "TASK_RUNNING"},
                    "message": "运行中的任务不能删除。",
                },
            )

        client = self.make_client(handler)
        with self.assertRaises(BackendHTTPError) as context:
            client.delete_task("task-1")
        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.code, "TASK_RUNNING")

    def test_success_returns_data(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"status": "running"},
                    "message": "ok",
                },
            )

        client = self.make_client(handler)
        self.assertEqual(client.health(), {"status": "running"})

    def test_invalid_json_error(self):
        def handler(request):
            return httpx.Response(200, text="not-json", headers={"Content-Type": "text/plain"})

        client = self.make_client(handler)
        with self.assertRaises(BackendResponseError):
            client.health()

    def test_auth_and_system_client_use_formal_api_paths(self):
        requests = []
        responses = {
            "/api/auth/status": {"login_ready": True},
            "/api/auth/login": {"profile_saved": True},
            "/api/system/open-save-directory": {"path": "D:\\mock\\videos"},
        }

        def handler(request):
            requests.append((request.method, request.url.path))
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": responses[request.url.path],
                    "message": "ok",
                },
            )

        client = self.make_client(handler)
        self.assertEqual(client.get_auth_status(), {"login_ready": True})
        self.assertEqual(client.login(), {"profile_saved": True})
        self.assertEqual(
            client.open_save_directory(),
            {"path": "D:\\mock\\videos"},
        )

        self.assertEqual(
            requests,
            [
                ("GET", "/api/auth/status"),
                ("POST", "/api/auth/login"),
                ("POST", "/api/system/open-save-directory"),
            ],
        )

    def test_login_uses_extended_timeout(self):
        client = BackendClient("http://backend.test")
        response = httpx.Response(
            200,
            json={
                "success": True,
                "data": {"profile_saved": True},
                "message": "ok",
            },
        )
        with patch.object(client._client, "request", return_value=response) as request_mock:
            result = client.login()

        self.assertEqual(result, {"profile_saved": True})
        self.assertEqual(
            request_mock.call_args.kwargs["timeout"],
            BACKEND_API_LOGIN_TIMEOUT,
        )

    def test_login_timeout_is_reported(self):
        def handler(request):
            raise httpx.ReadTimeout("login timeout", request=request)

        client = self.make_client(handler)
        with self.assertRaises(BackendTimeoutError):
            client.login()

    def test_video_query_parameters_are_forwarded(self):
        captured = {}

        def handler(request):
            captured.update(dict(request.url.params))
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "items": [],
                        "total": 0,
                        "page": 2,
                        "page_size": 25,
                        "total_pages": 0,
                    },
                    "message": "ok",
                },
            )

        client = self.make_client(handler)
        result = client.get_videos(
            keyword="keyword",
            status="pending",
            page=2,
            page_size=25,
            sort_by="title",
            sort_order="desc",
        )
        self.assertEqual(result["page"], 2)
        self.assertEqual(captured["keyword"], "keyword")
        self.assertEqual(captured["status"], "pending")
        self.assertEqual(captured["page"], "2")
        self.assertEqual(captured["page_size"], "25")
        self.assertEqual(captured["sort_by"], "title")
        self.assertEqual(captured["sort_order"], "desc")

    def test_capture_client_uses_formal_api_paths(self):
        requests = []
        responses = {
            "/api/capture/start": {"task_id": "capture-test", "status": "running"},
            "/api/capture/stop": {"stopping": True},
            "/api/capture/status": {
                "task_id": "capture-test",
                "status": "running",
                "message": "抓取中",
                "progress": {},
                "error": None,
                "stopping": False,
                "stop_requested_at": None,
            },
            "/api/capture/results": {"task_id": "capture-test", "items": [], "total": 0},
            "/api/capture/clear": {"cleared": True},
        }

        def handler(request):
            requests.append((request.method, request.url.path, request.content))
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": responses[request.url.path],
                    "message": "ok",
                },
            )

        client = self.make_client(handler)
        client.start_capture("https://www.douyin.com/", "recommend")
        client.stop_capture()
        client.capture_status()
        client.capture_results()
        client.clear_capture()

        self.assertEqual(
            [path for _, path, _ in requests],
            [
                "/api/capture/start",
                "/api/capture/stop",
                "/api/capture/status",
                "/api/capture/results",
                "/api/capture/clear",
            ],
        )

    def test_download_client_uses_formal_api_paths(self):
        requests = []
        responses = {
            "/api/downloads": {
                "items": [],
                "total": 0,
                "counts": {},
                "queue": {
                    "running": False,
                    "thread_alive": False,
                    "stopping": False,
                    "forced_task_count": 0,
                    "counts": {},
                },
            },
            "/api/downloads/status": {
                "running": False,
                "thread_alive": False,
                "stopping": False,
                "forced_task_count": 0,
                "counts": {},
            },
            "/api/downloads/start": {
                "started": True,
                "running": True,
                "queue": {
                    "running": True,
                    "thread_alive": True,
                    "stopping": False,
                    "forced_task_count": 0,
                    "counts": {},
                },
            },
            "/api/downloads/stop": {"stopping": True},
            "/api/downloads/tasks": {
                "created": [],
                "existing": [],
                "skipped": [],
                "created_count": 0,
            },
            "/api/downloads/immediate": {
                "queued": False,
                "reason": "running",
                "task": {
                    "task_id": "task-1",
                    "status": "running",
                },
            },
            "/api/downloads/tasks/task-1/retry": {
                "task_id": "task-1",
                "status": "waiting",
            },
            "/api/downloads/tasks/task-1": {"removed": True, "reason": "removed"},
            "/api/downloads/tasks/clear-completed": {"removed": 1},
        }

        def handler(request):
            requests.append((request.method, request.url.path))
            return httpx.Response(
                200,
                json={"success": True, "data": responses[request.url.path], "message": "ok"},
            )

        client = self.make_client(handler)
        client.downloads_snapshot()
        client.downloads_status()
        client.start_downloads()
        client.stop_downloads()
        client.queue_items([])
        client.immediate_download("7700000000000000001", "mock", False)
        client.retry_task("task-1")
        client.delete_task("task-1")
        client.clear_completed()

        self.assertEqual(
            [path for _, path in requests],
            [
                "/api/downloads",
                "/api/downloads/status",
                "/api/downloads/start",
                "/api/downloads/stop",
                "/api/downloads/tasks",
                "/api/downloads/immediate",
                "/api/downloads/tasks/task-1/retry",
                "/api/downloads/tasks/task-1",
                "/api/downloads/tasks/clear-completed",
            ],
        )


class StreamlitHTTPMigrationTests(unittest.TestCase):
    def setUp(self):
        st.cache_resource.clear()

    def select_workspace(self, at, value):
        at.segmented_control(key="workspace_section").set_value(value).run(timeout=60)
        return at

    def run_app(self, client):
        with patch("backend_client.client.create_backend_client", return_value=client):
            return AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)

    def test_app1_has_no_local_runtime_or_business_repository_calls(self):
        source = APP_PATH.read_text(encoding="utf-8")
        client_source = (PROJECT_ROOT / "backend_client" / "client.py").read_text(encoding="utf-8")
        forbidden = [
            "RuntimeServices(",
            "TaskManager(",
            "import threading",
            "threading.Thread",
            "get_video_library_df",
            "get_analytics_df",
            "get_downloaded_ids",
            "StealthySession",
        ]
        for token in forbidden:
            self.assertNotIn(token, source)
        self.assertIn("create_backend_client", source)
        self.assertNotIn("/api/bridge", source)
        self.assertNotIn("/api/bridge", client_source)

    def test_app2_is_absent_and_not_referenced_by_active_ui(self):
        self.assertFalse((PROJECT_ROOT / "app2.py").exists())
        self.assertNotIn("app2.py", APP_PATH.read_text(encoding="utf-8"))
        self.assertNotIn(
            "app2.py",
            (PROJECT_ROOT / "backend_client" / "client.py").read_text(encoding="utf-8"),
        )

    def test_streamlit_loads_auth_status_via_http(self):
        client = FakeBackendClient()
        at = self.run_app(client)
        self.assertEqual(len(at.exception), 0)
        self.assertIn(("get_auth_status",), client.calls)
        self.assertIn(("downloads_snapshot",), client.calls)

    def test_streamlit_login_success_uses_auth_api_and_refreshes_state(self):
        client = FakeBackendClient({"login_ready": False})
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            at.button(key="toolbar_login_btn").click().run(timeout=60)
            at.run(timeout=60)

        login_calls = [call for call in client.calls if call[0] == "login"]
        self.assertEqual(login_calls, [("login",)])
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.button(key="toolbar_login_btn").label, "重新登录")

    def test_streamlit_login_failure_is_visible(self):
        client = FakeBackendClient({"login_ready": False})
        client.login_error = BackendHTTPError(
            409,
            "登录失败：浏览器 Profile 正在使用。",
            "LOGIN_FAILED",
        )
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            at.button(key="toolbar_login_btn").click().run(timeout=60)

        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("登录失败" in item.value for item in at.error))

    def test_streamlit_open_directory_uses_system_api(self):
        client = FakeBackendClient()
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            at.button(key="toolbar_folder_btn").click().run(timeout=60)

        calls = [call for call in client.calls if call[0] == "open_save_directory"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(at.exception), 0)

    def test_streamlit_capture_start_uses_http_once(self):
        client = FakeBackendClient()
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            at.button(key="capture_start_btn").click().run(timeout=60)
            at.run(timeout=60)
        calls = [call for call in client.calls if call[0] == "start_capture"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(at.exception), 0)

    def test_streamlit_download_start_uses_http_once(self):
        client = FakeBackendClient()
        client.downloads_data["counts"]["waiting"] = 1
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            at.button(key="dock_start_btn").click().run(timeout=60)
            at.run(timeout=60)
        calls = [call for call in client.calls if call[0] == "start_downloads"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(at.exception), 0)

    def test_streamlit_download_stop_uses_http(self):
        client = FakeBackendClient()
        client.downloads_data["queue"].update(
            {"running": True, "thread_alive": True, "stopping": False}
        )
        client.downloads_data["counts"]["running"] = 1
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            stop_button = at.button(key="dock_stop_btn")
            self.assertFalse(stop_button.disabled)
            stop_button.click().run(timeout=60)
        calls = [call for call in client.calls if call[0] == "stop_downloads"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(at.exception), 0)

    def test_streamlit_immediate_download_uses_http(self):
        client = FakeBackendClient(base_auth_status())
        client.videos = [
            {
                "video_id": "7700000000000000001",
                "title": "mock",
                "status": "pending",
                "status_label": "待下载",
                "action_label": "可加入队列",
                "task_id": None,
            }
        ]
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            self.select_workspace(at, "下载")
            at.text_input(key="download_id_input").input("7700000000000000001").run(timeout=60)
            at.button(key="download_single_btn").click().run(timeout=60)
        calls = [call for call in client.calls if call[0] == "immediate_download"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(at.exception), 0)

    def test_streamlit_video_search_and_pagination_use_http(self):
        client = FakeBackendClient()
        client.videos = [
            {
                "video_id": f"77000000000000{index:04d}",
                "title": f"视频 {index}",
                "status": "pending",
                "status_label": "待下载",
                "action_label": "可加入队列",
                "task_id": None,
            }
            for index in range(60)
        ]
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            self.select_workspace(at, "视频库")
            at.text_input(key="library_search_text").input("视频 42").run(timeout=60)
            at.button(key="library_next_page")
            search_calls = [
                call for call in client.calls
                if call[0] == "get_videos" and call[1] == "视频 42"
            ]

            client.calls.clear()
            at.text_input(key="library_search_text").input("").run(timeout=60)
            at.button(key="library_next_page").click().run(timeout=60)
            page_calls = [
                call for call in client.calls
                if call[0] == "get_videos" and call[3] == 2 and call[4] == 50
            ]

        self.assertTrue(search_calls)
        self.assertTrue(page_calls)
        self.assertEqual(len(at.exception), 0)

    def test_streamlit_analytics_and_refresh_use_formal_apis(self):
        client = FakeBackendClient()
        client.analytics_items = [
            {
                "video_id": "7700000000000000009",
                "author_name": "作者",
                "video_title": "标题",
                "digg_count": "1",
                "comment_count": "2",
                "collect_count": "3",
                "share_count": "4",
            }
        ]
        with patch("backend_client.client.create_backend_client", return_value=client):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60).run(timeout=60)
            self.select_workspace(at, "视频库")
            client.calls.clear()
            at.text_input(key="analytics_search_text").input("作者").run(timeout=60)
            analytics_search_calls = [
                call for call in client.calls
                if call[0] == "get_analytics" and call[1] == "作者"
            ]
            client.calls.clear()
            at.button(key="toolbar_refresh_btn").click().run(timeout=60)

        self.assertTrue(analytics_search_calls)
        self.assertTrue(any(call[0] == "get_auth_status" for call in client.calls))
        self.assertTrue(any(call[0] == "get_analytics" for call in client.calls))
        self.assertTrue(any(call[0] == "get_video_summary" for call in client.calls))
        self.assertTrue(any(call[0] == "downloads_snapshot" for call in client.calls))
        self.assertEqual(len(at.exception), 0)

    def test_backend_unavailable_is_visible_and_does_not_fallback(self):
        at = self.run_app(UnavailableBackendClient())
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(
            any("Backend 服务不可用" in item.value for item in at.error)
        )


if __name__ == "__main__":
    unittest.main()
