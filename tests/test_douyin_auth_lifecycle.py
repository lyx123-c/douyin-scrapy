import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services import douyin_auth


class FakeTaskManager:
    def is_capture_running(self):
        return False

    def is_download_running(self):
        return False


class FakePage:
    def __init__(self, close_after=None, started_event=None, wait_seconds=0.01):
        self.close_after = close_after
        self.started_event = started_event
        self.wait_seconds = wait_seconds
        self.wait_calls = 0
        self.closed = False
        self.listeners = []
        self.front_calls = 0

    def on(self, event, callback):
        if event == "close":
            self.listeners.append(callback)

    def bring_to_front(self):
        self.front_calls += 1

    def is_closed(self):
        return self.closed

    def wait_for_timeout(self, timeout_ms):
        self.wait_calls += 1
        if self.started_event is not None:
            self.started_event.set()
        time.sleep(self.wait_seconds)
        if self.close_after is not None and self.wait_calls >= self.close_after:
            self.closed = True
            for listener in self.listeners:
                listener(self)
            raise RuntimeError("Target page, context or browser has been closed")


class FakeSession:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        return False

    def fetch(self, url, page_action=None, wait=0):
        if page_action is not None:
            page_action(self.page)
        if self.page.is_closed():
            raise RuntimeError("Target page, context or browser has been closed")


class FakeProcess:
    def __init__(self, cmdline, children=None, name="chrome.exe"):
        self.info = {"pid": 100, "name": name, "cmdline": cmdline}
        self._children = children or []
        self.killed = False

    def children(self, recursive=True):
        return list(self._children)

    def kill(self):
        self.killed = True


class DouyinAuthLifecycleTests(unittest.TestCase):
    def test_profile_state_distinguishes_missing_empty_ready_and_unknown(self):
        with TemporaryDirectory(prefix="douyin_auth_state_") as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing"
            empty = root / "empty"
            ready = root / "ready"
            unknown = root / "unknown"
            empty.mkdir()
            ready.mkdir()
            unknown.mkdir()
            (ready / "Cookies").write_bytes(b"profile-data")
            (unknown / "Cookies").write_bytes(b"profile-data")

            with patch.object(douyin_auth, "USER_DATA_DIR", str(missing)):
                self.assertEqual(douyin_auth.login_profile_state(), "missing")
            with patch.object(douyin_auth, "USER_DATA_DIR", str(empty)):
                self.assertEqual(douyin_auth.login_profile_state(), "missing")
            with patch.object(douyin_auth, "USER_DATA_DIR", str(ready)):
                self.assertEqual(douyin_auth.login_profile_state(), "ready")
            with (
                patch.object(douyin_auth, "USER_DATA_DIR", str(unknown)),
                patch.object(Path, "iterdir", side_effect=OSError("denied")),
            ):
                self.assertEqual(douyin_auth.login_profile_state(), "unknown")

    def test_window_close_releases_session_and_lock(self):
        with TemporaryDirectory(prefix="douyin_auth_close_") as temp_dir:
            profile = Path(temp_dir) / "profile"
            profile.mkdir()
            marker = profile / "Cookies"
            marker.write_bytes(b"existing-cookie-data")

            first_page = FakePage(close_after=1)
            first_session = FakeSession(first_page)
            second_page = FakePage(close_after=None, wait_seconds=0.001)
            second_session = FakeSession(second_page)

            with (
                patch.object(douyin_auth, "USER_DATA_DIR", str(profile)),
                patch.object(douyin_auth, "LOGIN_WAIT_SECONDS", 0.03),
                patch.object(douyin_auth, "cleanup_residual_login_processes", return_value=0),
                patch.object(
                    douyin_auth,
                    "StealthySession",
                    side_effect=[first_session, second_session],
                ),
            ):
                with self.assertRaises(douyin_auth.LoginBrowserClosedError):
                    douyin_auth.login_douyin(FakeTaskManager())
                self.assertTrue(first_session.closed)

                result = douyin_auth.login_douyin(FakeTaskManager())

            self.assertEqual(result, str(profile.resolve()))
            self.assertTrue(second_session.closed)
            self.assertEqual(marker.read_bytes(), b"existing-cookie-data")

    def test_normal_timeout_closes_session_and_preserves_profile(self):
        with TemporaryDirectory(prefix="douyin_auth_timeout_") as temp_dir:
            profile = Path(temp_dir) / "profile"
            profile.mkdir()
            marker = profile / "Login Data"
            marker.write_bytes(b"persistent-profile")
            page = FakePage(close_after=None, wait_seconds=0.001)
            session = FakeSession(page)

            with (
                patch.object(douyin_auth, "USER_DATA_DIR", str(profile)),
                patch.object(douyin_auth, "LOGIN_WAIT_SECONDS", 0.02),
                patch.object(douyin_auth, "cleanup_residual_login_processes", return_value=0),
                patch.object(douyin_auth, "StealthySession", return_value=session),
            ):
                result = douyin_auth.login_douyin(FakeTaskManager())

            self.assertEqual(result, str(profile.resolve()))
            self.assertTrue(session.closed)
            self.assertEqual(marker.read_bytes(), b"persistent-profile")

    def test_concurrent_login_is_rejected_while_browser_is_running(self):
        with TemporaryDirectory(prefix="douyin_auth_duplicate_") as temp_dir:
            profile = Path(temp_dir) / "profile"
            page_started = threading.Event()
            first_page = FakePage(
                close_after=None,
                started_event=page_started,
                wait_seconds=0.01,
            )
            first_session = FakeSession(first_page)
            errors = []

            with (
                patch.object(douyin_auth, "USER_DATA_DIR", str(profile)),
                patch.object(douyin_auth, "LOGIN_WAIT_SECONDS", 1.0),
                patch.object(douyin_auth, "LOGIN_PROFILE_LOCK_TIMEOUT", 0.05),
                patch.object(douyin_auth, "cleanup_residual_login_processes", return_value=0),
                patch.object(douyin_auth, "StealthySession", return_value=first_session),
            ):
                def run_first_login():
                    try:
                        douyin_auth.login_douyin(FakeTaskManager())
                    except Exception as exc:
                        errors.append(exc)

                thread = threading.Thread(target=run_first_login, daemon=True)
                thread.start()
                self.assertTrue(page_started.wait(timeout=2))
                with self.assertRaisesRegex(RuntimeError, "Profile 正被占用"):
                    douyin_auth.login_douyin(FakeTaskManager())
                thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(first_session.closed)

    def test_residual_cleanup_only_kills_exact_profile_processes(self):
        exact_profile = r"D:\下载\PythonProject4\爬虫\douyin_user_data"
        child = FakeProcess(["--type=renderer"], name="chrome.exe")
        matching = FakeProcess(
            ["chrome.exe", f"--user-data-dir={exact_profile}"],
            children=[child],
        )
        unrelated = FakeProcess(
            ["chrome.exe", r"--user-data-dir=C:\Users\ASUS\ChromeProfile"],
        )
        unrelated_browser = FakeProcess(
            ["msedge.exe", f"--user-data-dir={exact_profile}"],
            name="msedge.exe",
        )

        with patch.object(
            douyin_auth.psutil,
            "process_iter",
            return_value=[matching, unrelated, unrelated_browser],
        ):
            killed = douyin_auth.cleanup_residual_login_processes(exact_profile)

        self.assertEqual(killed, 2)
        self.assertTrue(matching.killed)
        self.assertTrue(child.killed)
        self.assertFalse(unrelated.killed)
        self.assertFalse(unrelated_browser.killed)

    def test_profile_path_parser_supports_equals_and_separate_argument(self):
        expected = douyin_auth._normalized_path(r"D:\profile\douyin")
        self.assertTrue(
            douyin_auth._process_uses_profile(
                ["chrome.exe", r"--user-data-dir=D:\profile\douyin"],
                expected,
            )
        )
        self.assertTrue(
            douyin_auth._process_uses_profile(
                ["chrome.exe", "--user-data-dir", r"D:\profile\douyin"],
                expected,
            )
        )
        self.assertFalse(
            douyin_auth._process_uses_profile(
                ["chrome.exe", r"--user-data-dir=D:\profile\other"],
                expected,
            )
        )


if __name__ == "__main__":
    unittest.main()
