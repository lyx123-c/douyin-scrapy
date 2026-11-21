import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from . import runtime_test_environment as _test_runtime_environment
except ImportError:
    import runtime_test_environment as _test_runtime_environment

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.services import douyin_auth
from backend.services import douyin_scraper
from backend.services import downloader
from backend.services.runtime import (
    RuntimeServices,
    RuntimeShutdownState,
    RuntimeWorkUnavailableError,
)
from backend.services.task_manager import TaskManager


@contextmanager
def fake_ownership():
    yield "test-owner"


class FakeTaskManager:
    def __init__(self, save_result=True):
        self.save_result = save_result
        self.save_calls = 0

    def save(self):
        self.save_calls += 1
        return self.save_result


class FakeService:
    def __init__(self, task_manager, running_after=False):
        self.task_manager = task_manager
        self.running_after = running_after
        self.shutdown_calls = 0

    def shutdown(self, timeout):
        self.shutdown_calls += 1
        return True

    def is_running(self):
        return self.running_after


def make_fake_runtime(save_result=True):
    manager = FakeTaskManager(save_result=save_result)
    capture = FakeService(manager)
    download = FakeService(manager)
    return RuntimeServices(
        task_manager=manager,
        capture_service=capture,
        download_queue_service=download,
    )


def force_runtime_state(runtime, state):
    with runtime._shutdown_lock:
        runtime._shutdown_state = state


class RuntimeWorkGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(prefix="runtime_work_gate_")
        self.store_path = Path(self.temp_dir.name) / "download_queue.json"
        self.manager = TaskManager(str(self.store_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_work_lease_allowed_only_while_running(self):
        runtime = make_fake_runtime()

        with runtime.work_lease():
            self.assertEqual(runtime.active_work_starts, 1)
        self.assertEqual(runtime.active_work_starts, 0)

        force_runtime_state(runtime, RuntimeShutdownState.STOPPING)
        with self.assertRaises(RuntimeWorkUnavailableError):
            with runtime.work_lease():
                self.fail("STOPPING must reject work")

        force_runtime_state(runtime, RuntimeShutdownState.STOP_FAILED)
        with self.assertRaises(RuntimeWorkUnavailableError):
            with runtime.work_lease():
                self.fail("STOP_FAILED must reject work")

        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)
        with self.assertRaises(RuntimeWorkUnavailableError):
            with runtime.work_lease():
                self.fail("STOPPED must reject work")

    def test_shutdown_waits_for_active_start_then_rejects_new_starts(self):
        runtime = make_fake_runtime()
        lease_acquired = threading.Event()
        release_lease = threading.Event()
        shutdown_waiting = threading.Event()
        owner_result = []
        original_wait = runtime._wait_for_work_starts

        def hold_lease():
            with runtime.work_lease():
                lease_acquired.set()
                self.assertTrue(release_lease.wait(timeout=2))

        def observed_wait(deadline):
            shutdown_waiting.set()
            return original_wait(deadline)

        lease_thread = threading.Thread(target=hold_lease, daemon=True)
        lease_thread.start()
        self.assertTrue(lease_acquired.wait(timeout=2))

        with patch.object(
            runtime,
            "_wait_for_work_starts",
            side_effect=observed_wait,
        ):
            owner = threading.Thread(
                target=lambda: owner_result.append(runtime.shutdown(timeout=2)),
                daemon=True,
            )
            owner.start()
            self.assertTrue(shutdown_waiting.wait(timeout=2))
            self.assertEqual(
                runtime.shutdown_state,
                RuntimeShutdownState.STOPPING,
            )
            self.assertEqual(runtime.capture_service.shutdown_calls, 0)
            self.assertEqual(runtime.download_queue_service.shutdown_calls, 0)

            with self.assertRaises(RuntimeWorkUnavailableError):
                with runtime.work_lease():
                    self.fail("new work must be rejected during STOPPING")

            release_lease.set()
            owner.join(timeout=2)

        lease_thread.join(timeout=2)
        self.assertFalse(owner.is_alive())
        self.assertFalse(lease_thread.is_alive())
        self.assertEqual(owner_result, [True])
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

    def test_multiple_start_requests_are_all_rejected_during_stopping(self):
        runtime = make_fake_runtime()
        lease_acquired = threading.Event()
        release_lease = threading.Event()
        shutdown_waiting = threading.Event()
        original_wait = runtime._wait_for_work_starts

        def hold_lease():
            with runtime.work_lease():
                lease_acquired.set()
                self.assertTrue(release_lease.wait(timeout=2))

        def observed_wait(deadline):
            shutdown_waiting.set()
            return original_wait(deadline)

        lease_thread = threading.Thread(target=hold_lease, daemon=True)
        lease_thread.start()
        self.assertTrue(lease_acquired.wait(timeout=2))

        with patch.object(
            runtime,
            "_wait_for_work_starts",
            side_effect=observed_wait,
        ):
            owner_result = []
            owner = threading.Thread(
                target=lambda: owner_result.append(runtime.shutdown(timeout=2)),
                daemon=True,
            )
            owner.start()
            self.assertTrue(shutdown_waiting.wait(timeout=2))

            caller_count = 5
            barrier = threading.Barrier(caller_count)

            def attempt_work(_index):
                barrier.wait(timeout=2)
                try:
                    with runtime.work_lease():
                        return True
                except RuntimeWorkUnavailableError:
                    return False

            with ThreadPoolExecutor(max_workers=caller_count) as pool:
                results = list(pool.map(attempt_work, range(caller_count)))

            self.assertEqual(results, [False] * caller_count)
            self.assertEqual(runtime.capture_service.shutdown_calls, 0)
            self.assertEqual(runtime.download_queue_service.shutdown_calls, 0)

            release_lease.set()
            owner.join(timeout=2)

        lease_thread.join(timeout=2)
        self.assertFalse(owner.is_alive())
        self.assertFalse(lease_thread.is_alive())
        self.assertEqual(owner_result, [True])

    def test_capture_start_gate_rejects_without_side_effects(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)
        service = runtime.capture_service

        with (
            patch("backend.services.capture_service.threading.Thread") as thread_mock,
            patch.object(service, "_kill_browser") as cleanup_mock,
        ):
            with self.assertRaises(RuntimeWorkUnavailableError):
                service.start_capture("https://www.douyin.com/", "recommend")

        thread_mock.assert_not_called()
        cleanup_mock.assert_not_called()
        self.assertIsNone(self.manager.get_capture_snapshot())

    def test_download_start_gate_rejects_without_side_effects(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)
        service = runtime.download_queue_service

        with (
            patch(
                "backend.services.download_queue_service.threading.Thread"
            ) as thread_mock,
            patch.object(self.manager, "start_download_queue") as start_mock,
        ):
            with self.assertRaises(RuntimeWorkUnavailableError):
                service.start()

        thread_mock.assert_not_called()
        start_mock.assert_not_called()

    def test_batch_queue_gate_rejects_without_task_side_effects(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)
        service = runtime.download_queue_service

        with patch.object(self.manager, "enqueue_download") as enqueue_mock:
            with self.assertRaises(RuntimeWorkUnavailableError):
                service.queue_items(
                    [
                        {
                            "video_id": "7400000000000000201",
                            "title": "blocked",
                        }
                    ]
                )

        enqueue_mock.assert_not_called()
        self.assertEqual(self.manager.get_tasks(), [])

    def test_immediate_download_gate_rejects_without_task_side_effects(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)
        service = runtime.download_queue_service

        with (
            patch.object(self.manager, "find_by_video_id") as find_mock,
            patch.object(self.manager, "enqueue_download") as enqueue_mock,
        ):
            with self.assertRaises(RuntimeWorkUnavailableError):
                service.queue_immediate(
                    "7400000000000000202",
                    "blocked",
                    False,
                )

        find_mock.assert_not_called()
        enqueue_mock.assert_not_called()
        self.assertEqual(self.manager.get_tasks(), [])

    def test_retry_gate_rejects_without_task_state_change(self):
        task, _ = self.manager.enqueue_download(
            "7400000000000000203",
            "retry blocked",
        )
        self.manager.mark_failed(task["task_id"], "mock failure")
        before = self.manager.get_task(task["task_id"])
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)

        with self.assertRaises(RuntimeWorkUnavailableError):
            runtime.download_queue_service.retry_task(task["task_id"])

        self.assertEqual(self.manager.get_task(task["task_id"]), before)

    def test_capture_worker_rejects_browser_creation_after_shutdown(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)

        with (
            patch.object(
                douyin_scraper,
                "browser_profile_guard",
                side_effect=lambda *args, **kwargs: nullcontext(),
            ),
            patch.object(douyin_scraper, "StealthySession") as session_mock,
        ):
            generator = douyin_scraper.fetch_ids_task_generator(
                "https://www.douyin.com/",
                "recommend",
                lambda: True,
                work_gate=runtime.work_lease,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "Runtime 正在关闭或已经关闭",
            ):
                next(generator)

        session_mock.assert_not_called()

    def test_download_worker_rejects_browser_creation_after_shutdown(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)
        video_id = "7400000000000000299"
        save_dir = Path(self.temp_dir.name) / "videos"

        with (
            patch.object(downloader, "SAVE_DIR", str(save_dir)),
            patch.object(downloader, "DOWNLOAD_RETRY_COUNT", 1),
            patch.object(
                downloader,
                "is_already_downloaded",
                return_value=False,
            ),
            patch.object(
                downloader,
                "get_title_from_library",
                return_value=None,
            ),
            patch.object(
                downloader,
                "browser_profile_guard",
                side_effect=lambda *args, **kwargs: nullcontext(),
            ),
            patch.object(downloader, "StealthySession") as session_mock,
        ):
            success, message = downloader.download_video_task(
                video_id,
                force=True,
                work_gate=runtime.work_lease,
            )

        self.assertFalse(success)
        self.assertIn("Runtime 正在关闭或已经关闭", message)
        session_mock.assert_not_called()
        self.assertFalse(save_dir.exists())

    def test_login_gate_rejects_before_profile_or_browser_creation(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)
        profile = Path(self.temp_dir.name) / "profile"

        with (
            patch.object(douyin_auth, "USER_DATA_DIR", str(profile)),
            patch.object(
                douyin_auth,
                "cleanup_residual_login_processes",
            ) as cleanup_mock,
            patch.object(douyin_auth, "StealthySession") as session_mock,
        ):
            with self.assertRaises(RuntimeWorkUnavailableError):
                douyin_auth.login_douyin(
                    self.manager,
                    work_lease=runtime.work_lease,
                )

        self.assertFalse(profile.exists())
        cleanup_mock.assert_not_called()
        session_mock.assert_not_called()

    def test_all_api_work_entries_map_stopped_runtime_to_safe_error(self):
        runtime = RuntimeServices(task_manager=self.manager)
        force_runtime_state(runtime, RuntimeShutdownState.STOPPED)

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                fake_ownership,
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
            patch.object(
                runtime.capture_service,
                "start_capture",
            ) as capture_start_mock,
            patch.object(runtime.download_queue_service, "start") as start_mock,
            patch.object(
                runtime.download_queue_service,
                "queue_items",
            ) as queue_mock,
            patch.object(
                runtime.download_queue_service,
                "queue_immediate",
            ) as immediate_mock,
            patch.object(
                runtime.download_queue_service,
                "retry_task",
            ) as retry_mock,
            patch("backend.api.routes.auth.login_douyin") as login_mock,
            TestClient(app) as client,
        ):
            responses = [
                client.post(
                    "/api/capture/start",
                    json={
                        "target_url": "https://www.douyin.com/",
                        "mode": "recommend",
                    },
                ),
                client.post("/api/downloads/start"),
                client.post(
                    "/api/downloads/tasks",
                    json={
                        "items": [
                            {
                                "video_id": "7400000000000000204",
                                "title": "blocked",
                            }
                        ]
                    },
                ),
                client.post(
                    "/api/downloads/immediate",
                    json={
                        "video_id": "7400000000000000205",
                        "title": "blocked",
                        "force": False,
                    },
                ),
                client.post("/api/downloads/tasks/task-1/retry"),
                client.post("/api/auth/login"),
            ]

        self.assertTrue(
            all(response.status_code == 503 for response in responses)
        )
        self.assertTrue(
            all(
                response.json()["data"]["code"]
                == "RUNTIME_NOT_ACCEPTING_WORK"
                for response in responses
            )
        )
        capture_start_mock.assert_not_called()
        start_mock.assert_not_called()
        queue_mock.assert_not_called()
        immediate_mock.assert_not_called()
        retry_mock.assert_not_called()
        login_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
