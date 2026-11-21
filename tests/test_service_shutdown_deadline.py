import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from . import runtime_test_environment as _test_runtime_environment
except ImportError:
    import runtime_test_environment as _test_runtime_environment

from backend.services.capture_service import CaptureService
from backend.services.download_queue_service import DownloadQueueService
from backend.services.task_manager import TaskManager


class FakeThread:
    def __init__(self, *, exits_on_join=False, events=None):
        self.alive = True
        self.exits_on_join = exits_on_join
        self.join_timeouts = []
        self.events = events

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        if timeout is None:
            raise AssertionError("shutdown must never call join() without a timeout")
        self.join_timeouts.append(timeout)
        if self.events is not None:
            self.events.append(("join", timeout))
        if self.exits_on_join:
            self.alive = False


class ServiceShutdownDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(prefix="service_shutdown_deadline_")
        self.store_path = Path(self.temp_dir.name) / "download_queue.json"
        self.manager = TaskManager(str(self.store_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_capture_shutdown_requests_stop_then_joins_until_exit(self):
        self.assertIsNotNone(self.manager.start_capture())
        thread = FakeThread(exits_on_join=True)
        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        service._thread = thread

        self.assertIs(service.shutdown(timeout=0.2), True)

        self.assertTrue(self.manager.capture_stop_requested())
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(thread.join_timeouts), 1)
        self.assertGreaterEqual(thread.join_timeouts[0], 0.0)
        self.assertLessEqual(thread.join_timeouts[0], 0.2)
        self.assertIs(service._thread, thread)

    def test_capture_shutdown_timeout_keeps_live_thread_reference(self):
        self.assertIsNotNone(self.manager.start_capture())
        thread = FakeThread(exits_on_join=False)
        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        service._thread = thread

        self.assertIs(service.shutdown(timeout=0.05), False)

        self.assertTrue(self.manager.capture_stop_requested())
        self.assertTrue(thread.is_alive())
        self.assertEqual(len(thread.join_timeouts), 1)
        self.assertLessEqual(thread.join_timeouts[0], 0.05)
        self.assertIs(service._thread, thread)

    def test_capture_repeated_shutdown_does_not_create_timer_or_thread(self):
        self.assertIsNotNone(self.manager.start_capture())
        thread = FakeThread(exits_on_join=False)
        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        service._thread = thread

        with (
            patch("backend.services.capture_service.threading.Timer") as timer_mock,
            patch("backend.services.capture_service.threading.Thread") as thread_mock,
        ):
            first = service.shutdown(timeout=0.01)
            second = service.shutdown(timeout=0.01)

        self.assertIs(first, False)
        self.assertIs(second, False)
        timer_mock.assert_not_called()
        thread_mock.assert_not_called()
        self.assertEqual(len(thread.join_timeouts), 2)
        self.assertIs(service._thread, thread)

    def test_capture_stop_request_time_counts_against_deadline(self):
        self.assertIsNotNone(self.manager.start_capture())
        events = []
        thread = FakeThread(exits_on_join=False, events=events)
        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        service._thread = thread

        def delayed_stop():
            events.append(("stop", None))
            return True

        with (
            patch(
                "backend.services.capture_service.time.monotonic",
                side_effect=[100.0, 100.2],
            ),
            patch.object(service, "request_stop", side_effect=delayed_stop),
        ):
            result = service.shutdown(timeout=0.1)

        self.assertIs(result, False)
        self.assertEqual(events[0][0], "stop")
        self.assertEqual(events[1], ("join", 0.0))

    def test_capture_timeout_zero_still_requests_stop(self):
        self.assertIsNotNone(self.manager.start_capture())
        thread = FakeThread(exits_on_join=False)
        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        service._thread = thread

        self.assertIs(service.shutdown(timeout=0), False)

        self.assertTrue(self.manager.capture_stop_requested())
        self.assertEqual(thread.join_timeouts, [0.0])

    def test_capture_cannot_start_thread_after_shutdown(self):
        service = CaptureService(
            self.manager,
            stop_grace_seconds=0,
            kill_browser=lambda: None,
        )
        self.assertIs(service.shutdown(timeout=0), True)

        with (
            patch("backend.services.capture_service.threading.Thread") as thread_mock,
            patch.object(service, "_kill_browser") as kill_browser_mock,
        ):
            result = service.start_capture("https://www.douyin.com/", "recommend")

        self.assertIsNone(result)
        thread_mock.assert_not_called()
        kill_browser_mock.assert_not_called()

    def test_download_shutdown_requests_stop_then_joins_until_exit(self):
        thread = FakeThread(exits_on_join=True)
        stop_event = threading.Event()
        service = DownloadQueueService(self.manager)
        service._thread = thread
        service._stop_event = stop_event

        self.assertIs(service.shutdown(timeout=0.2), True)

        self.assertTrue(stop_event.is_set())
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(thread.join_timeouts), 1)
        self.assertGreaterEqual(thread.join_timeouts[0], 0.0)
        self.assertLessEqual(thread.join_timeouts[0], 0.2)
        self.assertIs(service._thread, thread)

    def test_download_shutdown_timeout_keeps_live_thread_reference(self):
        thread = FakeThread(exits_on_join=False)
        stop_event = threading.Event()
        service = DownloadQueueService(self.manager)
        service._thread = thread
        service._stop_event = stop_event

        self.assertIs(service.shutdown(timeout=0.05), False)

        self.assertTrue(stop_event.is_set())
        self.assertTrue(thread.is_alive())
        self.assertEqual(len(thread.join_timeouts), 1)
        self.assertLessEqual(thread.join_timeouts[0], 0.05)
        self.assertIs(service._thread, thread)

    def test_download_repeated_shutdown_only_joins_existing_thread(self):
        thread = FakeThread(exits_on_join=False)
        stop_event = threading.Event()
        service = DownloadQueueService(self.manager)
        service._thread = thread
        service._stop_event = stop_event

        with patch("backend.services.download_queue_service.threading.Thread") as thread_mock:
            first = service.shutdown(timeout=0.01)
            second = service.shutdown(timeout=0.01)

        self.assertIs(first, False)
        self.assertIs(second, False)
        thread_mock.assert_not_called()
        self.assertEqual(len(thread.join_timeouts), 2)
        self.assertIs(service._thread, thread)

    def test_download_stop_request_time_counts_against_deadline(self):
        events = []
        thread = FakeThread(exits_on_join=False, events=events)
        stop_event = threading.Event()
        service = DownloadQueueService(self.manager)
        service._thread = thread
        service._stop_event = stop_event

        def delayed_stop():
            events.append(("stop", None))
            stop_event.set()
            return True

        with (
            patch(
                "backend.services.download_queue_service.time.monotonic",
                side_effect=[200.0, 200.2],
            ),
            patch.object(service, "request_stop", side_effect=delayed_stop),
        ):
            result = service.shutdown(timeout=0.1)

        self.assertIs(result, False)
        self.assertEqual(events[0][0], "stop")
        self.assertEqual(events[1], ("join", 0.0))

    def test_download_timeout_zero_still_requests_stop(self):
        thread = FakeThread(exits_on_join=False)
        stop_event = threading.Event()
        service = DownloadQueueService(self.manager)
        service._thread = thread
        service._stop_event = stop_event

        self.assertIs(service.shutdown(timeout=0), False)

        self.assertTrue(stop_event.is_set())
        self.assertEqual(thread.join_timeouts, [0.0])

    def test_download_cannot_start_thread_after_shutdown(self):
        service = DownloadQueueService(self.manager)
        self.assertIs(service.shutdown(timeout=0), True)

        with (
            patch("backend.services.download_queue_service.threading.Thread") as thread_mock,
            patch.object(self.manager, "start_download_queue") as manager_start_mock,
        ):
            result = service.start()

        self.assertIs(result, False)
        thread_mock.assert_not_called()
        manager_start_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
