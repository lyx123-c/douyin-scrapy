import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import patch

try:
    from . import runtime_test_environment as _test_runtime_environment
except ImportError:
    import runtime_test_environment as _test_runtime_environment

from backend.api.main import lifespan


class FakeState:
    def __init__(self):
        object.__setattr__(self, "_values", {})
        object.__setattr__(self, "_fail_once", set())
        object.__setattr__(self, "_fail_none_once", set())

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if name in self._fail_once and value is not None:
            self._fail_once.discard(name)
            raise RuntimeError(f"{name} mount failed")
        if value is None and name in self._fail_none_once:
            self._fail_none_once.discard(name)
            raise RuntimeError(f"{name} cleanup failed")
        self._values[name] = value

    def fail_next_mount(self, name):
        self._fail_once.add(name)

    def fail_next_none(self, name):
        self._fail_none_once.add(name)


class FakeApp:
    def __init__(self):
        self.state = FakeState()


class FakeRuntime:
    def __init__(
        self,
        *,
        shutdown_result=True,
        shutdown_error=None,
        initial_state="running",
    ):
        self.task_manager = object()
        self.shutdown_result = shutdown_result
        self.shutdown_error = shutdown_error
        self.shutdown_calls = []
        self.state = initial_state

    def shutdown(self, timeout=30):
        self.shutdown_calls.append(timeout)
        if self.shutdown_error is not None:
            raise self.shutdown_error
        return self.shutdown_result


def make_ownership(events, error=None):
    @contextmanager
    def ownership(*_args, **_kwargs):
        if error is not None:
            raise error
        events.append("enter")
        try:
            yield "test-owner"
        finally:
            events.append("exit")

    return ownership


class LifespanCleanupTests(unittest.TestCase):
    def assert_runtime_state_cleared(self, app):
        self.assertIsNone(app.state.runtime)
        self.assertIsNone(app.state.task_manager)

    def test_normal_lifespan_cleans_runtime_and_releases_mutex(self):
        app = FakeApp()
        runtime = FakeRuntime()
        ownership_events = []

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
        ):
            async def scenario():
                async with lifespan(app):
                    self.assertIs(app.state.runtime, runtime)
                    self.assertIs(app.state.task_manager, runtime.task_manager)

            asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(runtime.shutdown_calls, [30.0])
        self.assertEqual(ownership_events, ["enter", "exit"])

    def test_runtime_constructor_failure_cleans_state_and_releases_mutex(self):
        app = FakeApp()
        ownership_events = []

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                side_effect=RuntimeError("construct failed"),
            ),
        ):
            async def scenario():
                async with lifespan(app):
                    self.fail("lifespan must not start")

            with self.assertRaisesRegex(RuntimeError, "construct failed"):
                asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(ownership_events, ["enter", "exit"])

    def test_app_state_mount_failure_cleans_created_runtime(self):
        app = FakeApp()
        runtime = FakeRuntime()
        ownership_events = []
        app.state.fail_next_mount("task_manager")

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
        ):
            async def scenario():
                async with lifespan(app):
                    self.fail("lifespan must not start")

            with self.assertRaisesRegex(RuntimeError, "task_manager mount failed"):
                asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(runtime.shutdown_calls, [30.0])
        self.assertEqual(ownership_events, ["enter", "exit"])

    def test_late_startup_failure_cleans_attached_runtime(self):
        app = FakeApp()
        runtime = FakeRuntime()
        ownership_events = []
        original_info = __import__(
            "backend.api.main",
            fromlist=["logger"],
        ).logger.info
        info_calls = 0

        def fail_first_info(*args, **kwargs):
            nonlocal info_calls
            info_calls += 1
            if info_calls == 1:
                raise RuntimeError("late startup failed")
            return original_info(*args, **kwargs)

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
            patch("backend.api.main.logger.info", side_effect=fail_first_info),
        ):
            async def scenario():
                async with lifespan(app):
                    self.fail("lifespan must not start")

            with self.assertRaisesRegex(RuntimeError, "late startup failed"):
                asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(runtime.shutdown_calls, [30.0])
        self.assertEqual(ownership_events, ["enter", "exit"])

    def test_startup_failure_is_not_masked_by_cleanup_exception(self):
        app = FakeApp()
        runtime = FakeRuntime(shutdown_error=RuntimeError("cleanup failed"))
        ownership_events = []
        app.state.fail_next_mount("task_manager")

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
            patch("backend.api.main.logger.exception") as log_mock,
        ):
            async def scenario():
                async with lifespan(app):
                    self.fail("lifespan must not start")

            with self.assertRaisesRegex(RuntimeError, "task_manager mount failed"):
                asyncio.run(scenario())

        self.assertEqual(runtime.shutdown_calls, [30.0])
        self.assert_runtime_state_cleared(app)
        self.assertEqual(ownership_events, ["enter", "exit"])
        self.assertTrue(log_mock.called)

    def test_startup_failure_with_shutdown_false_still_cleans_state(self):
        app = FakeApp()
        runtime = FakeRuntime(shutdown_result=False)
        ownership_events = []
        app.state.fail_next_mount("task_manager")

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
            patch("backend.api.main.logger.warning") as warning_mock,
        ):
            async def scenario():
                async with lifespan(app):
                    self.fail("lifespan must not start")

            with self.assertRaisesRegex(RuntimeError, "task_manager mount failed"):
                asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(runtime.shutdown_calls, [30.0])
        self.assertEqual(ownership_events, ["enter", "exit"])
        self.assertTrue(warning_mock.called)

    def test_shutdown_false_clears_state_without_faking_runtime_state(self):
        app = FakeApp()
        runtime = FakeRuntime(shutdown_result=False)

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership([]),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
        ):
            async def scenario():
                async with lifespan(app):
                    pass

            asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(runtime.state, "running")
        self.assertEqual(runtime.shutdown_calls, [30.0])

    def test_shutdown_exception_is_logged_and_state_is_cleared(self):
        app = FakeApp()
        runtime = FakeRuntime(shutdown_error=RuntimeError("shutdown failed"))

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership([]),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
            patch("backend.api.main.logger.exception") as log_mock,
        ):
            async def scenario():
                async with lifespan(app):
                    pass

            asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(runtime.state, "running")
        self.assertTrue(log_mock.called)

    def test_lifespan_body_exception_is_preserved_over_shutdown_exception(self):
        app = FakeApp()
        runtime = FakeRuntime(shutdown_error=RuntimeError("cleanup failed"))
        ownership_events = []

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
            patch("backend.api.main.logger.exception"),
        ):
            async def scenario():
                async with lifespan(app):
                    raise RuntimeError("application failed")

            with self.assertRaisesRegex(RuntimeError, "application failed"):
                asyncio.run(scenario())

        self.assert_runtime_state_cleared(app)
        self.assertEqual(runtime.shutdown_calls, [30.0])
        self.assertEqual(ownership_events, ["enter", "exit"])

    def test_app_state_cleanup_exception_does_not_mask_body_exception(self):
        app = FakeApp()
        runtime = FakeRuntime()

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership([]),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                return_value=runtime,
            ),
            patch("backend.api.main.logger.exception") as log_mock,
        ):
            async def scenario():
                async with lifespan(app):
                    app.state.fail_next_none("runtime")
                    raise RuntimeError("application failed")

            with self.assertRaisesRegex(RuntimeError, "application failed"):
                asyncio.run(scenario())

        self.assertIsNone(app.state.task_manager)
        self.assertTrue(log_mock.called)
        self.assertEqual(runtime.shutdown_calls, [30.0])

    def test_mutex_acquisition_failure_does_not_create_runtime(self):
        app = FakeApp()

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership([], error=RuntimeError("mutex busy")),
            ),
            patch("backend.api.main.RuntimeServices") as runtime_mock,
        ):
            async def scenario():
                async with lifespan(app):
                    self.fail("lifespan must not start")

            with self.assertRaisesRegex(RuntimeError, "mutex busy"):
                asyncio.run(scenario())

        runtime_mock.assert_not_called()
        self.assert_runtime_state_cleared(app)

    def test_repeated_lifespan_uses_new_runtime_and_clears_each_time(self):
        app = FakeApp()
        runtimes = [FakeRuntime(), FakeRuntime()]
        ownership_events = []

        with (
            patch(
                "backend.api.main.acquire_backend_runtime_ownership",
                make_ownership(ownership_events),
            ),
            patch(
                "backend.api.main.ensure_runtime_directories",
            ),
            patch(
                "backend.api.main.RuntimeServices",
                side_effect=runtimes,
            ),
        ):
            async def scenario():
                async with lifespan(app):
                    self.assertIs(app.state.runtime, runtimes[0])
                self.assert_runtime_state_cleared(app)
                async with lifespan(app):
                    self.assertIs(app.state.runtime, runtimes[1])
                self.assert_runtime_state_cleared(app)

            asyncio.run(scenario())

        self.assertEqual(ownership_events, ["enter", "exit", "enter", "exit"])
        self.assertEqual(runtimes[0].shutdown_calls, [30.0])
        self.assertEqual(runtimes[1].shutdown_calls, [30.0])


if __name__ == "__main__":
    unittest.main()
