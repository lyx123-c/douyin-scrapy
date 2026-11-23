import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from backend.api.schemas import VideoPageData
from backend.services.video_query_service import VideoQueryService


class VideoQueryServiceTests(unittest.TestCase):
    def test_list_videos_normalizes_missing_task_id_for_response_model(self):
        library = pd.DataFrame(
            [
                {
                    "视频ID": "7400000000000000001",
                    "标题": "测试视频",
                }
            ]
        )
        task_manager = Mock()
        task_manager.get_tasks.return_value = []
        runtime = SimpleNamespace(task_manager=task_manager)
        service = VideoQueryService(runtime)

        with (
            patch(
                "backend.services.video_query_service.get_video_library_df",
                return_value=library,
            ),
            patch(
                "backend.services.video_query_service.get_downloaded_ids",
                return_value=set(),
            ),
        ):
            result = service.list_videos(page=1, page_size=50)

        self.assertIsNone(result["items"][0]["task_id"])
        validated = VideoPageData.model_validate(result)
        self.assertIsNone(validated.items[0].task_id)

    def test_list_videos_keeps_task_ids_when_only_some_videos_have_tasks(self):
        library = pd.DataFrame(
            [
                {"视频ID": "7400000000000000001", "标题": "有任务"},
                {"视频ID": "7400000000000000002", "标题": "无任务"},
            ]
        )
        task_manager = Mock()
        task_manager.get_tasks.return_value = [
            {
                "video_id": "7400000000000000001",
                "task_id": "task-1",
                "status": "success",
            }
        ]
        runtime = SimpleNamespace(task_manager=task_manager)
        service = VideoQueryService(runtime)

        with (
            patch(
                "backend.services.video_query_service.get_video_library_df",
                return_value=library,
            ),
            patch(
                "backend.services.video_query_service.get_downloaded_ids",
                return_value=set(),
            ),
        ):
            result = service.list_videos(page=1, page_size=50)

        self.assertEqual(result["items"][0]["task_id"], "task-1")
        self.assertIsNone(result["items"][1]["task_id"])
        validated = VideoPageData.model_validate(result)
        self.assertEqual(validated.items[0].task_id, "task-1")
        self.assertIsNone(validated.items[1].task_id)


if __name__ == "__main__":
    unittest.main()
