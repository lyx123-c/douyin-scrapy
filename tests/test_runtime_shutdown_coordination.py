import threading
import unittest
from unittest.mock import patch

try:
    from . import runtime_test_environment as _test_runtime_environment
except ImportError:
    import runtime_test_environment as _test_runtime_environment

from backend.services.runtime import RuntimeServices, RuntimeShutdownState


class FakeTaskManager:
    def __init__(self, events=None, save_result=True, save_error=None):
        self.events = events if events is not None else []
        self.save_result = save_result
        self.save_error = save_error
        self.save_calls = 0

    def save(self):
        self.save_calls += 1
        self.events.append(("task_manager", "save"))
        if self.save_error is not None:
            raise self.save_error
        return self.save_result


class FakeService:
    def __init__(
        self,
        name,
        task_manager,
        events=None,
        *,
        shutdown_result=True,
        running_after=False,
        shutdown_error=None,
        running_error=None,
        on_shutdown=None,
    ):
        self.name = name
        self.task_manager = task_manager
        self.events = events if events is not None else []
        self.shutdown_result = shutdown_result
        self.running_after = running_after
        self.shutdown_error = shutdown_error
        self.running_error = running_error
        self.on_shutdown = on_shutdown
        self.shutdown_timeouts = []
        self.is_running_calls = 0

    def shutdown(self, timeout):
        self.shutdown_timeouts.append(timeout)
        self.events.append((self.name, "shutdown", timeout))
        if self.on_shutdown is not None:
            self.on_shutdown(timeout)
        if self.shutdown_error is not None:
            raise self.shutdown_error
        return self.shutdown_result

    def is_running(self):
        self.is_running_calls += 1
        if self.running_error is not None:
            raise self.running_error
        return self.running_after


def make_runtime(task_manager, capture, download):
    return RuntimeServices(
        task_manager=task_manager,
        capture_service=capture,
        download_queue_service=download,
    )


class RuntimeShutdownCoordinationTests(unittest.TestCase):
    def test_normal_shutdown_closes_capture_then_download_then_saves(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService("capture", manager, events)
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertTrue(runtime.is_accepting_work)
        self.assertTrue(runtime.shutdown(timeout=2))

        shutdown_events = [
            event for event in events
            if event[1] in {"shutdown", "save"}
        ]
        self.assertEqual(
            [event[0] for event in shutdown_events],
            ["capture", "download", "task_manager"],
        )
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)
        self.assertTrue(runtime.is_shutdown)
        self.assertFalse(runtime.is_accepting_work)
        self.assertTrue(runtime.shutdown_report["capture"]["success"])
        self.assertTrue(runtime.shutdown_report["download"]["success"])
        self.assertTrue(runtime.shutdown_report["task_manager"]["saved"])

    def test_capture_failure_does_not_skip_download_or_save(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService(
            "capture",
            manager,
            events,
            shutdown_result=False,
        )
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))

        self.assertEqual(len(capture.shutdown_timeouts), 1)
        self.assertEqual(len(download.shutdown_timeouts), 1)
        self.assertEqual(manager.save_calls, 1)
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)
        self.assertFalse(runtime.shutdown_report["capture"]["success"])

    def test_capture_exception_does_not_skip_download_or_save(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService(
            "capture",
            manager,
            events,
            shutdown_error=RuntimeError("capture boom"),
        )
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))

        self.assertEqual(len(download.shutdown_timeouts), 1)
        self.assertEqual(manager.save_calls, 1)
        self.assertIn(
            "capture boom",
            runtime.shutdown_report["capture"]["error"],
        )

    def test_download_failure_returns_failed_runtime_state(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService("capture", manager, events)
        download = FakeService(
            "download",
            manager,
            events,
            shutdown_result=False,
        )
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)
        self.assertFalse(runtime.shutdown_report["download"]["success"])
        self.assertEqual(manager.save_calls, 1)

    def test_still_running_service_is_never_reported_stopped(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService(
            "capture",
            manager,
            events,
            running_after=True,
        )
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))

        report = runtime.shutdown_report
        self.assertTrue(report["capture"]["shutdown_returned"])
        self.assertTrue(report["capture"]["running_after_shutdown"])
        self.assertFalse(report["capture"]["success"])
        self.assertFalse(report["all_threads_stopped"])
        self.assertFalse(runtime.is_shutdown)

    def test_capture_time_consumption_reduces_download_budget(self):
        clock = [100.0]

        def advance_clock(_timeout):
            clock[0] = 104.0

        events = []
        manager = FakeTaskManager(events)
        capture = FakeService(
            "capture",
            manager,
            events,
            on_shutdown=advance_clock,
        )
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        with patch(
            "backend.services.runtime.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            self.assertTrue(runtime.shutdown(timeout=10))

        self.assertAlmostEqual(capture.shutdown_timeouts[0], 10.0)
        self.assertAlmostEqual(download.shutdown_timeouts[0], 6.0)
        self.assertFalse(runtime.shutdown_report["deadline_exceeded"])

    def test_timeout_zero_is_passed_to_both_services(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService("capture", manager, events)
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        result = runtime.shutdown(timeout=0)

        self.assertFalse(result)
        self.assertEqual(capture.shutdown_timeouts, [0.0])
        self.assertEqual(download.shutdown_timeouts, [0.0])
        self.assertTrue(runtime.shutdown_report["deadline_exceeded"])

    def test_exhausted_deadline_marks_failure_even_if_services_stop(self):
        clock = [200.0]

        def exhaust(_timeout):
            clock[0] = 205.0

        events = []
        manager = FakeTaskManager(events)
        capture = FakeService(
            "capture",
            manager,
            events,
            on_shutdown=exhaust,
        )
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        with patch(
            "backend.services.runtime.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            self.assertFalse(runtime.shutdown(timeout=5))

        self.assertEqual(download.shutdown_timeouts, [0.0])
        self.assertTrue(runtime.shutdown_report["deadline_exceeded"])
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)

    def test_task_manager_save_false_fails_shutdown(self):
        events = []
        manager = FakeTaskManager(events, save_result=False)
        capture = FakeService("capture", manager, events)
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))
        self.assertFalse(runtime.shutdown_report["task_manager"]["saved"])

    def test_task_manager_exception_is_captured_and_does_not_escape(self):
        events = []
        manager = FakeTaskManager(
            events,
            save_error=OSError("save failed"),
        )
        capture = FakeService("capture", manager, events)
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))

        report = runtime.shutdown_report
        self.assertFalse(report["task_manager"]["saved"])
        self.assertIn("save failed", report["task_manager"]["error"])
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)

    def test_multiple_failures_are_all_retained(self):
        events = []
        manager = FakeTaskManager(events, save_result=False)
        capture = FakeService(
            "capture",
            manager,
            events,
            shutdown_error=RuntimeError("capture failure"),
        )
        download = FakeService(
            "download",
            manager,
            events,
            running_after=True,
        )
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))

        report = runtime.shutdown_report
        self.assertIn("capture failure", report["capture"]["error"])
        self.assertFalse(report["download"]["success"])
        self.assertTrue(report["download"]["running_after_shutdown"])
        self.assertFalse(report["task_manager"]["saved"])

    def test_repeated_successful_shutdown_does_not_repeat_work(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService("capture", manager, events)
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertTrue(runtime.shutdown(timeout=2))
        self.assertTrue(runtime.shutdown(timeout=2))

        self.assertEqual(len(capture.shutdown_timeouts), 1)
        self.assertEqual(len(download.shutdown_timeouts), 1)
        self.assertEqual(manager.save_calls, 1)

    def test_failed_shutdown_can_retry_against_current_state(self):
        events = []
        manager = FakeTaskManager(events)
        capture = FakeService(
            "capture",
            manager,
            events,
            shutdown_result=False,
            running_after=True,
        )
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))
        capture.shutdown_result = True

        def stop_capture(_timeout):
            capture.running_after = False

        capture.on_shutdown = stop_capture
        self.assertTrue(runtime.shutdown(timeout=2))

        self.assertEqual(len(capture.shutdown_timeouts), 2)
        self.assertEqual(len(download.shutdown_timeouts), 1)
        self.assertTrue(runtime.shutdown_report["download"]["skipped"])
        self.assertGreaterEqual(download.is_running_calls, 3)
        self.assertEqual(manager.save_calls, 2)
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

    def test_concurrent_shutdown_waiter_does_not_run_second_coordination(self):
        events = []
        manager = FakeTaskManager(events)
        started = threading.Event()
        release = threading.Event()

        def block_capture(_timeout):
            started.set()
            release.wait(timeout=2)

        capture = FakeService(
            "capture",
            manager,
            events,
            on_shutdown=block_capture,
        )
        download = FakeService("download", manager, events)
        runtime = make_runtime(manager, capture, download)
        owner_result = []
        waiter_result = []

        owner = threading.Thread(
            target=lambda: owner_result.append(runtime.shutdown(timeout=2)),
            daemon=True,
        )
        waiter = threading.Thread(
            target=lambda: waiter_result.append(runtime.shutdown(timeout=2)),
            daemon=True,
        )
        owner.start()
        self.assertTrue(started.wait(timeout=1))
        waiter.start()
        release.set()
        owner.join(timeout=2)
        waiter.join(timeout=2)

        self.assertFalse(owner.is_alive())
        self.assertFalse(waiter.is_alive())
        self.assertEqual(owner_result, [True])
        self.assertEqual(waiter_result, [True])
        self.assertEqual(len(capture.shutdown_timeouts), 1)
        self.assertEqual(len(download.shutdown_timeouts), 1)
        self.assertEqual(manager.save_calls, 1)


if __name__ == "__main__":
    unittest.main()
