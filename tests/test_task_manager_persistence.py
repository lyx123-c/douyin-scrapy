import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services.task_manager import TaskManager


class TaskManagerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(prefix="task_manager_persistence_")
        self.store_path = Path(self.temp_dir.name) / "download_queue.json"
        self.manager = TaskManager(str(self.store_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_queue(self, tasks):
        self.store_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated_at": "2026-01-01T00:00:00",
                    "tasks": tasks,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _temporary_queue_files(self):
        return list(self.store_path.parent.glob(".download_queue_*.tmp"))

    def test_normal_save_returns_true_and_writes_file(self):
        self.assertFalse(self.store_path.exists())

        self.assertIs(self.manager.save(), True)

        self.assertTrue(self.store_path.is_file())
        payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"version", "updated_at", "tasks"})
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["tasks"], [])

    def test_file_write_failure_returns_false_and_preserves_file(self):
        self.assertTrue(self.manager.save())
        before = self.store_path.read_bytes()

        with (
            patch(
                "backend.services.task_manager.json.dump",
                side_effect=OSError("simulated write failure"),
            ),
            patch("backend.services.task_manager.logger.exception") as log_mock,
        ):
            saved = self.manager.save()

        self.assertIs(saved, False)
        self.assertEqual(self.store_path.read_bytes(), before)
        self.assertEqual(self._temporary_queue_files(), [])
        log_mock.assert_called_once()

    def test_atomic_replace_failure_returns_false_and_preserves_file(self):
        self.assertTrue(self.manager.save())
        before = self.store_path.read_bytes()

        with (
            patch(
                "backend.services.task_manager.os.replace",
                side_effect=OSError("simulated replace failure"),
            ),
            patch("backend.services.task_manager.logger.exception") as log_mock,
        ):
            saved = self.manager.save()

        self.assertIs(saved, False)
        self.assertEqual(self.store_path.read_bytes(), before)
        self.assertEqual(self._temporary_queue_files(), [])
        log_mock.assert_called_once()

    def test_json_format_and_task_fields_remain_compatible(self):
        task = self.manager.add_task(
            "task-compatible",
            "download",
            video_id="7400000000000000101",
            title="兼容性测试",
        )
        self.assertIs(self.manager.save(), True)
        payload = json.loads(self.store_path.read_text(encoding="utf-8"))

        self.assertEqual(set(payload), {"version", "updated_at", "tasks"})
        self.assertEqual(len(payload["tasks"]), 1)
        stored = payload["tasks"][0]
        self.assertEqual(stored["task_id"], task["task_id"])
        self.assertEqual(stored["task_type"], "download")
        self.assertEqual(stored["video_id"], "7400000000000000101")
        self.assertEqual(stored["title"], "兼容性测试")
        self.assertEqual(stored["status"], "waiting")
        self.assertEqual(stored["progress"], 0.0)
        self.assertEqual(stored["error"], None)

    def test_running_task_recovery_remains_interrupted(self):
        self._write_queue(
            [
                {
                    "task_id": "task-running",
                    "task_type": "download",
                    "video_id": "7400000000000000102",
                    "title": "恢复测试",
                    "status": "running",
                    "progress": 0.5,
                    "message": "正在执行",
                    "error": None,
                },
                {
                    "task_id": "task-waiting",
                    "task_type": "download",
                    "video_id": "7400000000000000103",
                    "title": "等待测试",
                    "status": "waiting",
                    "progress": 0.0,
                    "message": "等待执行",
                    "error": None,
                },
            ]
        )

        recovered = TaskManager(str(self.store_path))

        self.assertEqual(
            recovered.get_task("task-running")["status"],
            "interrupted",
        )
        self.assertEqual(
            recovered.get_task("task-waiting")["status"],
            "waiting",
        )
        persisted = json.loads(self.store_path.read_text(encoding="utf-8"))
        statuses = {
            task["task_id"]: task["status"]
            for task in persisted["tasks"]
        }
        self.assertEqual(statuses["task-running"], "interrupted")
        self.assertEqual(statuses["task-waiting"], "waiting")

        reloaded = TaskManager(str(self.store_path))
        self.assertEqual(
            reloaded.get_task("task-running")["status"],
            "interrupted",
        )

    def test_repeated_save_returns_true_and_keeps_valid_json(self):
        for _ in range(3):
            self.assertIs(self.manager.save(), True)
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertIsInstance(payload["tasks"], list)

        self.assertEqual(self._temporary_queue_files(), [])

    def test_concurrent_save_calls_are_serialized_and_succeed(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.manager.save(), range(16)))

        self.assertTrue(all(result is True for result in results))
        payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(self._temporary_queue_files(), [])

    def test_throttled_dump_reports_that_no_write_occurred(self):
        self.assertIs(self.manager._dump(force=False), True)
        before = self.store_path.read_bytes()

        self.assertIs(self.manager._dump(force=False), False)

        self.assertEqual(self.store_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
