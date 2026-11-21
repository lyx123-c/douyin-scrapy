import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services.capture_service import CaptureService
from backend.services.download_queue_service import DownloadQueueService
from backend.services.runtime import RuntimeServices
from backend.services.task_manager import TaskManager


def wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class RuntimeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(prefix="runtime_services_test_")
        self.store_path = str(Path(self.temp_dir.name) / "download_queue.json")
        self.manager = TaskManager(self.store_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_runtime_uses_one_task_manager_and_does_not_start_threads(self):
        runtime = RuntimeServices(task_manager=self.manager)
        self.assertIs(runtime.task_manager, self.manager)
        self.assertIs(runtime.capture_service.task_manager, self.manager)
        self.assertIs(runtime.download_queue_service.task_manager, self.manager)
        self.assertFalse(runtime.capture_service.is_running())
        self.assertFalse(runtime.download_queue_service.is_running())
        self.assertEqual(runtime.capture_service.get_public_status()["status"], "idle")
        self.assertEqual(runtime.capture_service.get_results()["total"], 0)
        self.assertFalse(
            any(
                thread.name.startswith(("douyin-capture-", "douyin-download-queue"))
                for thread in threading.enumerate()
            )
        )
        self.assertTrue(runtime.shutdown(timeout=1))
        self.assertTrue(runtime.is_shutdown)

    def test_task_retry_and_delete_rules_remain_compatible(self):
        task, _ = self.manager.enqueue_download("7400000000000000006", "retry mock")
        self.manager.mark_running(task["task_id"])
        self.assertFalse(self.manager.remove_task(task["task_id"]))
        self.manager.mark_failed(task["task_id"], "mock failure")
        retried = self.manager.retry_task(task["task_id"])
        self.assertEqual(retried["status"], "waiting")
        self.assertTrue(self.manager.remove_task(task["task_id"]))
        self.assertIsNone(self.manager.get_task(task["task_id"]))

    def test_capture_duplicate_start_stop_event_and_shutdown(self):
        stop_seen = threading.Event()

        def fake_fetch(target_url, mode, stop_signal_checker, progress_callback=None):
            if progress_callback:
                progress_callback("抓取中")
            while not stop_signal_checker():
                time.sleep(0.01)
            stop_seen.set()
            yield [{"视频ID": "7400000000000000001", "标题": "mock"}]

        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        with patch(
            "backend.services.capture_service.fetch_ids_task_generator",
            side_effect=fake_fetch,
        ):
            task_id = service.start_capture("https://www.douyin.com/", "recommend")
            self.assertIsNotNone(task_id)
            self.assertIsNone(service.start_capture("https://www.douyin.com/", "recommend"))
            self.assertTrue(service.request_stop())
            self.assertFalse(service.request_stop())
            self.assertTrue(self.manager.capture_stop_requested())
            self.assertTrue(stop_seen.wait(timeout=2))
            self.assertTrue(service.shutdown(timeout=2))

        status = service.get_status()
        self.assertEqual(status["status"], "success")
        self.assertEqual(len(status["results"]), 1)
        public_status = service.get_public_status()
        self.assertEqual(public_status["status"], "success")
        self.assertNotIn("results", public_status)
        results = service.get_results()
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["items"][0]["video_id"], "7400000000000000001")
        self.assertFalse(service.is_running())

    def test_capture_concurrent_start_only_creates_one_thread(self):
        def fake_fetch(target_url, mode, stop_signal_checker, progress_callback=None):
            while not stop_signal_checker():
                time.sleep(0.01)
            yield []

        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        with (
            patch(
                "backend.services.capture_service.fetch_ids_task_generator",
                side_effect=fake_fetch,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(
                pool.map(
                    lambda _: service.start_capture("https://www.douyin.com/", "recommend"),
                    range(2),
                )
            )
            self.assertEqual(sum(item is None for item in results), 1)
            self.assertEqual(sum(item is not None for item in results), 1)
            service.request_stop()
            self.assertTrue(service.shutdown(timeout=2))

    def test_capture_thread_start_failure_rolls_back_running_state(self):
        class FailingThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("thread start failed")

            def is_alive(self):
                return False

        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        with (
            patch("backend.services.capture_service.threading.Thread", FailingThread),
            patch("backend.services.capture_service.logger.exception"),
        ):
            with self.assertRaises(RuntimeError):
                service.start_capture("https://www.douyin.com/", "recommend")

        status = service.get_status()
        self.assertEqual(status["status"], "failed")
        self.assertIn("线程启动失败", status["error"])
        self.assertFalse(self.manager.is_capture_running())

    def test_capture_stop_watchdog_kills_browser_if_still_running(self):
        stopped = threading.Event()
        release = threading.Event()
        killed = threading.Event()

        def fake_fetch(target_url, mode, stop_signal_checker, progress_callback=None):
            while not stop_signal_checker():
                time.sleep(0.01)
            stopped.set()
            release.wait(timeout=2)
            yield []

        service = CaptureService(
            self.manager,
            stop_grace_seconds=0.05,
            kill_browser=killed.set,
        )
        with patch(
            "backend.services.capture_service.fetch_ids_task_generator",
            side_effect=fake_fetch,
        ):
            self.assertIsNotNone(service.start_capture("https://www.douyin.com/", "recommend"))
            self.assertTrue(service.request_stop())
            self.assertTrue(stopped.wait(timeout=2))
            self.assertTrue(killed.wait(timeout=2))
            release.set()
            self.assertTrue(service.shutdown(timeout=2))
        self.assertEqual(service.get_status()["status"], "success")

    def test_download_duplicate_start_and_success(self):
        task, created = self.manager.enqueue_download(
            "7400000000000000002",
            "download mock",
        )
        self.assertTrue(created)
        started = threading.Event()
        release = threading.Event()

        def fake_download(video_id, force=False, progress_callback=None):
            self.assertFalse(force)
            started.set()
            release.wait(timeout=3)
            if progress_callback:
                progress_callback({"progress": 1.0, "message": "完成"})
            return True, "mock done"

        service = DownloadQueueService(self.manager)
        with (
            patch(
                "backend.services.download_queue_service.is_already_downloaded",
                return_value=False,
            ),
            patch(
                "backend.services.download_queue_service.download_video_task",
                side_effect=fake_download,
            ),
        ):
            self.assertTrue(service.start())
            self.assertTrue(started.wait(timeout=2))
            self.assertFalse(service.start())
            release.set()
            self.assertTrue(wait_until(lambda: not service.is_running(), timeout=3))

        self.assertEqual(self.manager.get_task(task["task_id"])["status"], "success")
        self.assertFalse(self.manager.is_download_running())

    def test_concurrent_download_task_add_is_deduplicated(self):
        service = DownloadQueueService(self.manager)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(
                pool.map(
                    lambda _: service.queue_items(
                        [{"video_id": "7400000000000000007", "title": "same"}]
                    )["created_count"],
                    range(24),
                )
            )
        self.assertEqual(sum(results), 1)
        tasks = self.manager.get_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["video_id"], "7400000000000000007")

    def test_concurrent_download_start_and_stop(self):
        self.manager.enqueue_download("7400000000000000008", "concurrent")
        started = threading.Event()
        release = threading.Event()

        def fake_download(video_id, force=False, progress_callback=None):
            started.set()
            release.wait(timeout=3)
            return True, "done"

        service = DownloadQueueService(self.manager)
        with (
            patch(
                "backend.services.download_queue_service.is_already_downloaded",
                return_value=False,
            ),
            patch(
                "backend.services.download_queue_service.download_video_task",
                side_effect=fake_download,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            start_results = list(pool.map(lambda _: service.start(), range(2)))
            self.assertEqual(start_results.count(True), 1)
            self.assertTrue(started.wait(timeout=2))
            stop_results = list(pool.map(lambda _: service.request_stop(), range(2)))
            self.assertEqual(stop_results.count(True), 1)
            release.set()
            self.assertTrue(service.shutdown(timeout=3))

    def test_force_download_flag_bypasses_skip_without_json_field(self):
        task, _ = self.manager.enqueue_download(
            "7400000000000000005",
            "force mock",
        )
        observed_force = []

        def fake_download(video_id, force=False, progress_callback=None):
            observed_force.append(force)
            return True, "force done"

        service = DownloadQueueService(self.manager)
        service.set_force_download(task["task_id"], True)
        with (
            patch(
                "backend.services.download_queue_service.is_already_downloaded",
                return_value=True,
            ),
            patch(
                "backend.services.download_queue_service.download_video_task",
                side_effect=fake_download,
            ),
        ):
            self.assertTrue(service.start())
            self.assertTrue(wait_until(lambda: not service.is_running(), timeout=3))

        self.assertEqual(observed_force, [True])
        self.assertEqual(self.manager.get_task(task["task_id"])["status"], "success")
        self.assertNotIn("force_download", self.manager.get_task(task["task_id"]))

    def test_download_thread_start_failure_rolls_back_running_flag(self):
        class FailingThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("download thread start failed")

            def is_alive(self):
                return False

        service = DownloadQueueService(self.manager)
        with (
            patch("backend.services.download_queue_service.threading.Thread", FailingThread),
            patch("backend.services.download_queue_service.logger.exception"),
        ):
            with self.assertRaises(RuntimeError):
                service.start()

        self.assertFalse(self.manager.is_download_running())
        self.assertFalse(service.get_status()["running"])

    def test_download_stop_finishes_current_task_before_exiting(self):
        first, _ = self.manager.enqueue_download(
            "7400000000000000003",
            "first",
        )
        second, _ = self.manager.enqueue_download(
            "7400000000000000004",
            "second",
        )
        started = threading.Event()
        release = threading.Event()

        def fake_download(video_id, force=False, progress_callback=None):
            self.assertFalse(force)
            started.set()
            release.wait(timeout=3)
            return True, "mock done"

        service = DownloadQueueService(self.manager)
        with (
            patch(
                "backend.services.download_queue_service.is_already_downloaded",
                return_value=False,
            ),
            patch(
                "backend.services.download_queue_service.download_video_task",
                side_effect=fake_download,
            ),
        ):
            self.assertTrue(service.start())
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(service.request_stop())
            release.set()
            self.assertTrue(service.shutdown(timeout=3))

        self.assertEqual(self.manager.get_task(first["task_id"])["status"], "success")
        self.assertEqual(self.manager.get_task(second["task_id"])["status"], "waiting")
        self.assertFalse(self.manager.is_download_running())

    def test_download_worker_unexpected_exit_cleans_running_state(self):
        service = DownloadQueueService(self.manager)
        with (
            patch.object(
                self.manager,
                "next_waiting_download",
                side_effect=RuntimeError("worker boom"),
            ),
            patch("backend.services.download_queue_service.logger.exception"),
        ):
            self.assertTrue(service.start())
            self.assertTrue(wait_until(lambda: not service.is_running(), timeout=3))

        self.assertFalse(self.manager.is_download_running())
        self.assertFalse(service.get_status()["running"])


if __name__ == "__main__":
    unittest.main()
