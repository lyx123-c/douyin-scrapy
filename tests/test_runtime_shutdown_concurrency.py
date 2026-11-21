import threading
import unittest
from unittest.mock import patch

try:
    from . import runtime_test_environment as _test_runtime_environment
except ImportError:
    import runtime_test_environment as _test_runtime_environment

from backend.services.runtime import RuntimeServices, RuntimeShutdownState


class FakeTaskManager:
    def __init__(self, *, save_result=True, save_error=None, on_save=None):
        self.save_result = save_result
        self.save_error = save_error
        self.on_save = on_save
        self._lock = threading.Lock()
        self.save_calls = 0

    def save(self):
        with self._lock:
            self.save_calls += 1
        if self.on_save is not None:
            self.on_save()
        if self.save_error is not None:
            raise self.save_error
        return self.save_result


class FakeService:
    def __init__(
        self,
        name,
        task_manager,
        *,
        shutdown_result=True,
        running_after=False,
        shutdown_error=None,
        running_error=None,
        on_shutdown=None,
    ):
        self.name = name
        self.task_manager = task_manager
        self.shutdown_result = shutdown_result
        self.running_after = running_after
        self.shutdown_error = shutdown_error
        self.running_error = running_error
        self.on_shutdown = on_shutdown
        self._lock = threading.Lock()
        self.shutdown_calls = 0
        self.is_running_calls = 0
        self.active_shutdowns = 0
        self.max_concurrent_shutdowns = 0

    def shutdown(self, timeout):
        with self._lock:
            self.shutdown_calls += 1
            self.active_shutdowns += 1
            if self.active_shutdowns > self.max_concurrent_shutdowns:
                self.max_concurrent_shutdowns = self.active_shutdowns
        try:
            if self.on_shutdown is not None:
                self.on_shutdown(timeout)
            if self.shutdown_error is not None:
                raise self.shutdown_error
            return self.shutdown_result
        finally:
            with self._lock:
                self.active_shutdowns -= 1

    def is_running(self):
        with self._lock:
            self.is_running_calls += 1
        if self.running_error is not None:
            raise self.running_error
        return self.running_after


def make_runtime(manager, capture, download):
    return RuntimeServices(
        task_manager=manager,
        capture_service=capture,
        download_queue_service=download,
    )


class RuntimeShutdownConcurrencyTests(unittest.TestCase):
    def test_two_threads_concurrent_shutdown_single_coordinator(self):
        barrier = threading.Barrier(2)
        entered = threading.Event()
        release = threading.Event()

        def hold_capture(_timeout):
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        manager = FakeTaskManager()
        capture = FakeService("capture", manager, on_shutdown=hold_capture)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        results = [None, None]

        def worker(idx):
            barrier.wait(timeout=2)
            results[idx] = runtime.shutdown(timeout=2)

        t1 = threading.Thread(target=worker, args=(0,), daemon=True)
        t2 = threading.Thread(target=worker, args=(1,), daemon=True)
        t1.start()
        t2.start()

        self.assertTrue(entered.wait(timeout=2))
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPING)
        self.assertFalse(runtime.is_accepting_work)
        release.set()

        t1.join(timeout=2)
        t2.join(timeout=2)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(results, [True, True])
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(capture.max_concurrent_shutdowns, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(download.max_concurrent_shutdowns, 1)
        self.assertEqual(manager.save_calls, 1)
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)
        self.assertEqual(runtime.shutdown_report["outcome"], "stopped")

    def test_five_callers_concurrent_shutdown_all_get_consistent_result(self):
        caller_count = 5
        barrier = threading.Barrier(caller_count)
        entered = threading.Event()
        release = threading.Event()

        def hold_capture(_timeout):
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        manager = FakeTaskManager()
        capture = FakeService("capture", manager, on_shutdown=hold_capture)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        results = [None] * caller_count

        def worker(idx):
            barrier.wait(timeout=2)
            results[idx] = runtime.shutdown(timeout=2)

        threads = [
            threading.Thread(target=worker, args=(i,), daemon=True)
            for i in range(caller_count)
        ]
        for thread in threads:
            thread.start()

        self.assertTrue(entered.wait(timeout=2))
        release.set()

        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.assertEqual(results, [True] * caller_count)
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 1)

    def test_waiter_blocks_during_stopping_and_wakes_on_completion(self):
        owner_entered = threading.Event()
        release_owner = threading.Event()
        waiter_started = threading.Event()
        waiter_done = threading.Event()

        def hold_capture(_timeout):
            owner_entered.set()
            self.assertTrue(release_owner.wait(timeout=2))

        manager = FakeTaskManager()
        capture = FakeService("capture", manager, on_shutdown=hold_capture)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        owner_result = []
        waiter_result = []

        def run_owner():
            owner_result.append(runtime.shutdown(timeout=2))

        def run_waiter():
            waiter_started.set()
            waiter_result.append(runtime.shutdown(timeout=2))
            waiter_done.set()

        owner = threading.Thread(target=run_owner, daemon=True)
        waiter = threading.Thread(target=run_waiter, daemon=True)

        owner.start()
        self.assertTrue(owner_entered.wait(timeout=2))

        waiter.start()
        self.assertTrue(waiter_started.wait(timeout=2))
        self.assertFalse(waiter_done.is_set())
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 0)
        self.assertEqual(manager.save_calls, 0)

        release_owner.set()
        self.assertTrue(waiter_done.wait(timeout=2))
        owner.join(timeout=2)
        waiter.join(timeout=2)

        self.assertEqual(owner_result, [True])
        self.assertEqual(waiter_result, [True])
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 1)

    def test_concurrent_shutdown_failure_returns_consistent_false(self):
        entered = threading.Event()
        waiters_registered = threading.Event()
        release = threading.Event()

        def hold_capture(_timeout):
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        manager = FakeTaskManager()
        capture = FakeService(
            "capture",
            manager,
            shutdown_result=False,
            on_shutdown=hold_capture,
        )
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        results = [None, None, None]

        def run_owner():
            results[0] = runtime.shutdown(timeout=2)

        def run_waiter(idx):
            self.assertTrue(entered.wait(timeout=2))
            results[idx] = runtime.shutdown(timeout=2)

        t1 = threading.Thread(target=run_owner, daemon=True)
        t2 = threading.Thread(target=run_waiter, args=(1,), daemon=True)
        t3 = threading.Thread(target=run_waiter, args=(2,), daemon=True)

        t1.start()
        self.assertTrue(entered.wait(timeout=2))
        active_round = runtime._active_shutdown
        self.assertIsNotNone(active_round)
        original_wait = active_round.done.wait
        waiter_count = 0
        waiter_count_lock = threading.Lock()
        waiters_registered = threading.Event()

        def intercept_wait(timeout=None):
            nonlocal waiter_count
            with waiter_count_lock:
                waiter_count += 1
                if waiter_count == 2:
                    waiters_registered.set()
            return original_wait(timeout=timeout)

        with patch.object(
            active_round.done,
            "wait",
            side_effect=intercept_wait,
        ):
            t2.start()
            t3.start()
            self.assertTrue(waiters_registered.wait(timeout=2))
            release.set()

            t1.join(timeout=2)
            t2.join(timeout=2)
            t3.join(timeout=2)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertFalse(t3.is_alive())

        self.assertEqual(results, [False, False, False])
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 1)
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)
        self.assertEqual(runtime.shutdown_report["outcome"], "failed")
        self.assertFalse(runtime.is_accepting_work)
        self.assertFalse(runtime.is_shutdown)

    def test_concurrent_shutdown_exception_returns_consistent_false_without_hanging(self):
        entered = threading.Event()
        release = threading.Event()

        def hold_and_fail(_timeout):
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        manager = FakeTaskManager(save_error=OSError("disk full"))
        capture = FakeService(
            "capture",
            manager,
            shutdown_error=RuntimeError("capture crash"),
            on_shutdown=hold_and_fail,
        )
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        results = [None, None, None]

        owner = threading.Thread(
            target=lambda: results.__setitem__(
                0,
                runtime.shutdown(timeout=2),
            ),
            daemon=True,
        )
        owner.start()
        self.assertTrue(entered.wait(timeout=2))

        active_round = runtime._active_shutdown
        self.assertIsNotNone(active_round)
        original_wait = active_round.done.wait
        waiter_count = 0
        waiter_count_lock = threading.Lock()
        waiters_registered = threading.Event()

        def intercept_wait(timeout=None):
            nonlocal waiter_count
            with waiter_count_lock:
                waiter_count += 1
                if waiter_count == 2:
                    waiters_registered.set()
            return original_wait(timeout=timeout)

        def run_waiter(idx):
            results[idx] = runtime.shutdown(timeout=2)

        waiter_threads = [
            threading.Thread(target=run_waiter, args=(i,), daemon=True)
            for i in (1, 2)
        ]

        with patch.object(
            active_round.done,
            "wait",
            side_effect=intercept_wait,
        ):
            for thread in waiter_threads:
                thread.start()
            self.assertTrue(waiters_registered.wait(timeout=2))
            release.set()

            owner.join(timeout=2)
            for thread in waiter_threads:
                thread.join(timeout=2)

        self.assertFalse(owner.is_alive())
        self.assertTrue(all(not thread.is_alive() for thread in waiter_threads))

        self.assertEqual(results, [False, False, False])
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 1)
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)
        self.assertEqual(runtime.shutdown_report["outcome"], "error")

    def test_waiter_does_not_block_indefinitely_when_timeout_expires(self):
        owner_entered = threading.Event()
        release_owner = threading.Event()

        def hold_capture(_timeout):
            owner_entered.set()
            self.assertTrue(release_owner.wait(timeout=2))

        manager = FakeTaskManager()
        capture = FakeService("capture", manager, on_shutdown=hold_capture)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        owner_result = []
        owner = threading.Thread(
            target=lambda: owner_result.append(runtime.shutdown(timeout=2)),
            daemon=True,
        )
        owner.start()
        self.assertTrue(owner_entered.wait(timeout=2))

        waiter_result = runtime.shutdown(timeout=0.01)
        self.assertFalse(waiter_result)
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPING)

        release_owner.set()
        owner.join(timeout=2)
        self.assertFalse(owner.is_alive())
        self.assertEqual(owner_result, [True])
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

    def test_stop_failed_retry_only_closes_unconfirmed_services_and_rechecks_confirmed(self):
        manager = FakeTaskManager()
        capture = FakeService("capture", manager, shutdown_result=False)
        capture = FakeService(
            "capture",
            manager,
            shutdown_result=False,
            running_after=True,
        )
        download = FakeService("download", manager, shutdown_result=True, running_after=False)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)
        self.assertFalse(runtime.is_accepting_work)
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 1)

        capture.shutdown_result = True

        def stop_capture(_timeout):
            capture.running_after = False

        capture.on_shutdown = stop_capture
        self.assertTrue(runtime.shutdown(timeout=2))
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)
        self.assertFalse(runtime.is_accepting_work)
        self.assertEqual(capture.shutdown_calls, 2)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertTrue(runtime.shutdown_report["download"]["skipped"])
        self.assertEqual(manager.save_calls, 2)

    def test_stop_failed_retry_recloses_service_if_recheck_sees_running(self):
        manager = FakeTaskManager()
        capture = FakeService("capture", manager, shutdown_result=False)
        capture = FakeService(
            "capture",
            manager,
            shutdown_result=False,
            running_after=True,
        )
        download = FakeService("download", manager, shutdown_result=True, running_after=False)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))
        self.assertEqual(download.shutdown_calls, 1)

        capture.shutdown_result = True

        def stop_capture_on_retry(_timeout):
            capture.running_after = False

        capture.on_shutdown = stop_capture_on_retry
        download.running_after = True

        def stop_download_on_retry(_timeout):
            download.running_after = False

        download.on_shutdown = stop_download_on_retry
        self.assertTrue(runtime.shutdown(timeout=2))
        self.assertEqual(capture.shutdown_calls, 2)
        self.assertEqual(download.shutdown_calls, 2)
        self.assertFalse(runtime.shutdown_report["download"]["skipped"])
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

    def test_round_one_waiter_keeps_round_one_result_even_if_round_two_succeeds(self):
        round1_entered = threading.Event()
        release_round1 = threading.Event()
        waiter_got_round1_event = threading.Event()
        allow_waiter_to_resume = threading.Event()

        def hold_round1(_timeout):
            round1_entered.set()
            self.assertTrue(release_round1.wait(timeout=2))

        manager = FakeTaskManager()
        capture = FakeService(
            "capture",
            manager,
            shutdown_result=False,
            on_shutdown=hold_round1,
        )
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        owner1_result = []
        waiter1_result = []

        owner1 = threading.Thread(
            target=lambda: owner1_result.append(runtime.shutdown(timeout=2)),
            daemon=True,
        )
        owner1.start()
        self.assertTrue(round1_entered.wait(timeout=2))

        round1_obj = runtime._active_shutdown
        self.assertIsNotNone(round1_obj)
        original_wait = round1_obj.done.wait

        def intercepted_wait(timeout=None):
            waiter_got_round1_event.set()
            completed = original_wait(timeout=timeout)
            self.assertTrue(allow_waiter_to_resume.wait(timeout=2))
            return completed

        with patch.object(round1_obj.done, "wait", side_effect=intercepted_wait):
            waiter1 = threading.Thread(
                target=lambda: waiter1_result.append(runtime.shutdown(timeout=2)),
                daemon=True,
            )
            waiter1.start()
            self.assertTrue(waiter_got_round1_event.wait(timeout=2))

            release_round1.set()
            owner1.join(timeout=2)
            self.assertEqual(owner1_result, [False])
            self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)

            capture.shutdown_result = True
            capture.on_shutdown = None
            round2_result = runtime.shutdown(timeout=2)
            self.assertTrue(round2_result)
            self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

            allow_waiter_to_resume.set()
            waiter1.join(timeout=2)

        self.assertFalse(waiter1.is_alive())
        self.assertEqual(waiter1_result, [False])

    def test_stopped_repeated_and_concurrent_calls_do_no_work(self):
        manager = FakeTaskManager()
        capture = FakeService("capture", manager)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        self.assertTrue(runtime.shutdown(timeout=2))
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

        barrier = threading.Barrier(4)
        results = [None] * 4

        def worker(idx):
            barrier.wait(timeout=2)
            results[idx] = runtime.shutdown(timeout=2)

        threads = [
            threading.Thread(target=worker, args=(i,), daemon=True)
            for i in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.assertEqual(results, [True, True, True, True])
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 1)

    def test_retry_skips_service_confirmed_stopped_despite_failed_return(self):
        manager = FakeTaskManager()
        capture = FakeService(
            "capture",
            manager,
            shutdown_result=False,
            running_after=False,
        )
        download = FakeService(
            "download",
            manager,
            shutdown_result=True,
            running_after=False,
        )
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=2))
        self.assertTrue(
            runtime.shutdown_report["capture"]["confirmed_stopped"]
        )
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)

        self.assertTrue(runtime.shutdown(timeout=2))
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 2)
        self.assertTrue(runtime.shutdown_report["capture"]["skipped"])
        self.assertTrue(runtime.shutdown_report["download"]["skipped"])

    def test_reentrant_shutdown_on_owner_thread_does_not_deadlock(self):
        nested_result = []
        manager = FakeTaskManager()
        capture = FakeService("capture", manager)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        def call_nested(_timeout):
            nested_result.append(runtime.shutdown(timeout=5))

        capture.on_shutdown = call_nested

        self.assertTrue(runtime.shutdown(timeout=2))
        self.assertEqual(nested_result, [False])
        self.assertEqual(capture.shutdown_calls, 1)
        self.assertEqual(download.shutdown_calls, 1)
        self.assertEqual(manager.save_calls, 1)
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

    def test_state_properties_do_not_deadlock_while_shutdown_is_in_progress(self):
        entered = threading.Event()
        release = threading.Event()

        def hold_capture(_timeout):
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        manager = FakeTaskManager()
        capture = FakeService("capture", manager, on_shutdown=hold_capture)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        owner = threading.Thread(
            target=lambda: runtime.shutdown(timeout=2),
            daemon=True,
        )
        owner.start()
        self.assertTrue(entered.wait(timeout=2))

        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPING)
        self.assertFalse(runtime.is_shutdown)
        self.assertFalse(runtime.is_accepting_work)
        self.assertEqual(runtime.shutdown_report, {})

        release.set()
        owner.join(timeout=2)
        self.assertFalse(owner.is_alive())
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOPPED)

    def test_timeout_outcome_classification(self):
        manager = FakeTaskManager()
        capture = FakeService("capture", manager)
        download = FakeService("download", manager)
        runtime = make_runtime(manager, capture, download)

        self.assertFalse(runtime.shutdown(timeout=0))
        self.assertEqual(runtime.shutdown_state, RuntimeShutdownState.STOP_FAILED)
        self.assertEqual(runtime.shutdown_report["outcome"], "timeout")


if __name__ == "__main__":
    unittest.main()
