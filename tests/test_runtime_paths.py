import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGED_ENV_KEYS = {
    "DOUYIN_APP_USER_DATA_DIR",
    "DOUYIN_VIDEO_DATA_DIR",
    "DOUYIN_TASK_DATA_DIR",
    "DOUYIN_BROWSER_PROFILE_DIR",
    "DOUYIN_SAVE_DIR",
    "DOUYIN_ANALYTICS_CSV",
    "DOUYIN_VIDEOS_CSV",
    "DOUYIN_DOWNLOAD_QUEUE_JSON",
    "DOUYIN_LOG_DIR",
    "FFMPEG_PATH",
}


def _isolated_environment(extra=None):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("DOUYIN_") or key in MANAGED_ENV_KEYS:
            env.pop(key, None)
    if extra:
        env.update({key: str(value) for key, value in extra.items()})
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_config_script(script, extra_env=None):
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(extra_env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Config subprocess failed with {result.returncode}:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


CONFIG_PATHS_SCRIPT = """
import json
import config

print(json.dumps(
    {name: str(value) for name, value in config.RUNTIME_PATHS.items()},
    ensure_ascii=False,
))
"""


class RuntimePathConfigTests(unittest.TestCase):
    def test_development_defaults_preserve_existing_locations(self):
        paths = _run_config_script(CONFIG_PATHS_SCRIPT)

        self.assertEqual(Path(paths["app_user_data"]), PROJECT_ROOT)
        self.assertEqual(Path(paths["video_data"]), PROJECT_ROOT)
        self.assertEqual(Path(paths["task_data"]), PROJECT_ROOT / "data")
        self.assertEqual(
            Path(paths["browser_profile"]),
            Path(r"D:\下载\PythonProject4\爬虫\douyin_user_data"),
        )
        self.assertEqual(Path(paths["video_save"]), PROJECT_ROOT / "mmp")
        self.assertEqual(
            Path(paths["analytics_csv"]),
            PROJECT_ROOT / "video_analytics.csv",
        )
        self.assertEqual(
            Path(paths["video_library_csv"]),
            PROJECT_ROOT / "douyin_videos.csv",
        )
        self.assertEqual(
            Path(paths["download_queue_json"]),
            PROJECT_ROOT / "data" / "download_queue.json",
        )
        self.assertEqual(Path(paths["logs"]), PROJECT_ROOT / "logs")
        self.assertEqual(
            Path(paths["ffmpeg"]),
            Path(r"E:\360Downloads\ffmpeg.exe"),
        )

    def test_app_data_root_switches_queue_profile_csv_and_logs(self):
        with TemporaryDirectory(prefix="runtime_paths_root_") as temp_dir:
            app_root = Path(temp_dir) / "AppData" / "DouyinVideoWorkbench"
            paths = _run_config_script(
                CONFIG_PATHS_SCRIPT,
                {
                    "DOUYIN_APP_USER_DATA_DIR": app_root,
                    "DOUYIN_SAVE_DIR": Path(temp_dir) / "custom-videos",
                },
            )

            self.assertEqual(Path(paths["app_user_data"]), app_root)
            self.assertEqual(Path(paths["video_data"]), app_root / "data")
            self.assertEqual(Path(paths["task_data"]), app_root / "tasks")
            self.assertEqual(
                Path(paths["browser_profile"]),
                app_root / "browser-profile",
            )
            self.assertEqual(
                Path(paths["analytics_csv"]),
                app_root / "data" / "video_analytics.csv",
            )
            self.assertEqual(
                Path(paths["video_library_csv"]),
                app_root / "data" / "douyin_videos.csv",
            )
            self.assertEqual(
                Path(paths["download_queue_json"]),
                app_root / "tasks" / "download_queue.json",
            )
            self.assertEqual(Path(paths["logs"]), app_root / "logs")
            self.assertEqual(
                Path(paths["video_save"]),
                Path(temp_dir) / "custom-videos",
            )

    def test_granular_overrides_keep_legacy_precedence(self):
        with TemporaryDirectory(prefix="runtime_paths_overrides_") as temp_dir:
            root = Path(temp_dir)
            overrides = {
                "DOUYIN_APP_USER_DATA_DIR": root / "app",
                "DOUYIN_VIDEO_DATA_DIR": root / "library",
                "DOUYIN_TASK_DATA_DIR": root / "queue-data",
                "DOUYIN_BROWSER_PROFILE_DIR": root / "profile",
                "DOUYIN_SAVE_DIR": root / "videos",
                "DOUYIN_ANALYTICS_CSV": root / "custom" / "analytics.csv",
                "DOUYIN_VIDEOS_CSV": root / "custom" / "videos.csv",
                "DOUYIN_DOWNLOAD_QUEUE_JSON": root / "custom" / "queue.json",
                "DOUYIN_LOG_DIR": root / "custom-logs",
                "FFMPEG_PATH": root / "bin" / "ffmpeg.exe",
            }
            paths = _run_config_script(CONFIG_PATHS_SCRIPT, overrides)

            self.assertEqual(Path(paths["video_data"]), root / "library")
            self.assertEqual(Path(paths["task_data"]), root / "queue-data")
            self.assertEqual(Path(paths["browser_profile"]), root / "profile")
            self.assertEqual(Path(paths["video_save"]), root / "videos")
            self.assertEqual(
                Path(paths["analytics_csv"]),
                root / "custom" / "analytics.csv",
            )
            self.assertEqual(
                Path(paths["video_library_csv"]),
                root / "custom" / "videos.csv",
            )
            self.assertEqual(
                Path(paths["download_queue_json"]),
                root / "custom" / "queue.json",
            )
            self.assertEqual(Path(paths["logs"]), root / "custom-logs")
            self.assertEqual(Path(paths["ffmpeg"]), root / "bin" / "ffmpeg.exe")

    def test_runtime_directories_are_created_without_replacing_data(self):
        with TemporaryDirectory(prefix="runtime_paths_create_") as temp_dir:
            root = Path(temp_dir) / "app"
            video_csv = root / "data" / "douyin_videos.csv"
            queue_json = root / "tasks" / "download_queue.json"
            video_csv.parent.mkdir(parents=True)
            queue_json.parent.mkdir(parents=True)
            video_csv.write_bytes(b"existing-video-data")
            queue_json.write_bytes(b"existing-queue-data")
            save_dir = Path(temp_dir) / "videos"

            result = _run_config_script(
                """
import json
import config

config.ensure_runtime_directories()
print(json.dumps({
    "created": all(path.is_dir() for path in [
        config.VIDEO_DATA_PATH,
        config.TASK_DATA_PATH,
        config.USER_DATA_PATH,
        config.SAVE_PATH,
        config.LOG_PATH,
    ]),
    "video_bytes": config.VIDEO_TITLE_CSV_PATH.read_bytes().decode("utf-8"),
    "queue_bytes": config.DOWNLOAD_QUEUE_PATH.read_bytes().decode("utf-8"),
}))
""",
                {
                    "DOUYIN_APP_USER_DATA_DIR": root,
                    "DOUYIN_SAVE_DIR": save_dir,
                },
            )

            self.assertTrue(result["created"])
            self.assertEqual(result["video_bytes"], "existing-video-data")
            self.assertEqual(result["queue_bytes"], "existing-queue-data")
            self.assertTrue((root / "browser-profile").is_dir())
            self.assertTrue((root / "logs").is_dir())
            self.assertTrue(save_dir.is_dir())

    def test_runtime_and_task_manager_use_the_same_configured_queue(self):
        with TemporaryDirectory(prefix="runtime_paths_queue_") as temp_dir:
            app_root = Path(temp_dir) / "app"
            result = _run_config_script(
                """
import json
import config
from backend.services.runtime import DEFAULT_QUEUE_STORE
from backend.services.task_manager import TaskManager

manager = TaskManager()
print(json.dumps({
    "config_queue": str(config.DOWNLOAD_QUEUE_PATH),
    "runtime_queue": str(DEFAULT_QUEUE_STORE),
    "task_manager_queue": manager._store_path,
}))
""",
                {"DOUYIN_APP_USER_DATA_DIR": app_root},
            )

            expected = str(app_root / "tasks" / "download_queue.json")
            self.assertEqual(result["config_queue"], expected)
            self.assertEqual(result["runtime_queue"], expected)
            self.assertEqual(result["task_manager_queue"], expected)

    def test_configured_repository_and_queue_paths_receive_writes(self):
        with TemporaryDirectory(prefix="runtime_paths_write_") as temp_dir:
            app_root = Path(temp_dir) / "app"
            result = _run_config_script(
                """
import json
import config
from backend.repositories.video_repository import merge_video_library
from backend.services.task_manager import TaskManager

video_id = "7400000000000000099"
merge_video_library([{"视频ID": video_id, "标题": "isolated"}])
manager = TaskManager()
manager.enqueue_download(video_id, "isolated")
manager.save()
print(json.dumps({
    "csv_exists": config.VIDEO_TITLE_CSV_PATH.is_file(),
    "queue_exists": config.DOWNLOAD_QUEUE_PATH.is_file(),
    "csv_path": str(config.VIDEO_TITLE_CSV_PATH),
    "queue_path": str(config.DOWNLOAD_QUEUE_PATH),
}))
""",
                {"DOUYIN_APP_USER_DATA_DIR": app_root},
            )

            self.assertTrue(result["csv_exists"])
            self.assertTrue(result["queue_exists"])
            self.assertEqual(
                Path(result["csv_path"]),
                app_root / "data" / "douyin_videos.csv",
            )
            self.assertEqual(
                Path(result["queue_path"]),
                app_root / "tasks" / "download_queue.json",
            )

    def test_capture_browser_cleanup_uses_exact_configured_profile(self):
        from backend.services import douyin_scraper

        expected = douyin_scraper._normalized_profile_path(
            r"C:\Users\Example\AppData\browser-profile"
        )
        self.assertTrue(
            douyin_scraper._process_uses_profile(
                [
                    "chrome.exe",
                    r"--user-data-dir=C:\Users\Example\AppData\browser-profile",
                ],
                expected,
            )
        )
        self.assertFalse(
            douyin_scraper._process_uses_profile(
                [
                    "chrome.exe",
                    r"--user-data-dir=C:\Users\Example\AppData\browser-profile-old",
                ],
                expected,
            )
        )
        self.assertTrue(
            douyin_scraper._process_uses_profile(
                [
                    "chrome.exe",
                    "--user-data-dir",
                    r"C:\Users\Example\AppData\browser-profile",
                ],
                expected,
            )
        )

    def test_ensure_creates_parents_for_explicit_file_overrides(self):
        with TemporaryDirectory(prefix="runtime_paths_file_parents_") as temp_dir:
            root = Path(temp_dir)
            analytics_csv = root / "custom-analytics" / "analytics.csv"
            videos_csv = root / "custom-library" / "videos.csv"
            queue_json = root / "custom-tasks" / "queue.json"
            result = _run_config_script(
                """
import json
import config

config.ensure_runtime_directories()
print(json.dumps({
    "analytics_parent": config.ANALYTICS_CSV_PATH.parent.is_dir(),
    "videos_parent": config.VIDEO_TITLE_CSV_PATH.parent.is_dir(),
    "queue_parent": config.DOWNLOAD_QUEUE_PATH.parent.is_dir(),
}))
""",
                {
                    "DOUYIN_APP_USER_DATA_DIR": root / "app",
                    "DOUYIN_ANALYTICS_CSV": analytics_csv,
                    "DOUYIN_VIDEOS_CSV": videos_csv,
                    "DOUYIN_DOWNLOAD_QUEUE_JSON": queue_json,
                },
            )

            self.assertEqual(result, {
                "analytics_parent": True,
                "videos_parent": True,
                "queue_parent": True,
            })


if __name__ == "__main__":
    unittest.main()
