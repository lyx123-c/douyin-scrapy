import json
import hashlib
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.repositories import video_repository
from backend.services.douyin_parser import (
    extract_cards_from_html,
    extract_ids_from_text,
)
from backend.services.douyin_scraper import fetch_ids_task_generator


VIDEO_ID_1 = "7700000000000000001"
VIDEO_ID_2 = "7700000000000000002"
VIDEO_ID_3 = "7700000000000000003"
VIDEO_ID_4 = "7700000000000000004"


@contextmanager
def fake_runtime_ownership(*args, **kwargs):
    yield "capture-extraction-test-owner"


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DouyinParserTests(unittest.TestCase):
    def setUp(self):
        self.ids = set()
        self.video_data = {}

    def test_user_page_extracts_video_links_and_deduplicates(self):
        html = f"""
        <html><body>
          <main data-e2e="user-post-list">
            <a href="/video/{VIDEO_ID_1}" title="视频一"></a>
            <a href="//www.douyin.com/video/{VIDEO_ID_2}">
              <img alt="视频二" />
            </a>
            <a href="/user/7700000000000000099">用户主页</a>
            <a href="/video/12345">无效视频</a>
          </main>
          <a href="/video/{VIDEO_ID_3}" title="页面其他区域">其他区域</a>
        </body></html>
        """

        extract_cards_from_html(html, self.ids, "user", self.video_data)
        extract_cards_from_html(html, self.ids, "user", self.video_data)

        self.assertEqual(self.ids, {VIDEO_ID_1, VIDEO_ID_2})
        self.assertEqual(self.video_data[VIDEO_ID_1], "视频一")
        self.assertEqual(self.video_data[VIDEO_ID_2], "视频二")

    def test_jingxuan_prefers_attribute_and_falls_back_to_card_link(self):
        html = f"""
        <html><body>
          <div class="discover-video-card-item" data-aweme-id="{VIDEO_ID_1}">
            <a href="//www.douyin.com/video/{VIDEO_ID_1}">重复视频</a>
          </div>
          <div class="discover-video-card-item">
            <a href="/video/{VIDEO_ID_2}" title="属性缺失的视频"></a>
          </div>
          <a href="/video/{VIDEO_ID_3}" title="卡片外视频"></a>
        </body></html>
        """

        extract_cards_from_html(html, self.ids, "jingxuan", self.video_data)

        self.assertEqual(self.ids, {VIDEO_ID_1, VIDEO_ID_2})
        self.assertNotIn(VIDEO_ID_3, self.ids)

    def test_jingxuan_link_fallback_without_cards(self):
        html = f"""
        <html><body>
          <a href="https://www.douyin.com/video/{VIDEO_ID_1}" title="兜底视频"></a>
        </body></html>
        """

        extract_cards_from_html(html, self.ids, "jingxuan", self.video_data)

        self.assertEqual(self.ids, {VIDEO_ID_1})

    def test_search_is_scoped_to_result_container_and_supports_dynamic_cards(self):
        first_html = f"""
        <html><body>
          <div id="search-result-container">
            <div class="search-result-card">
              <a href="/video/{VIDEO_ID_1}" title="搜索结果一"></a>
            </div>
            <div id="waterfall_item_{VIDEO_ID_2}" class="search-result-card">
              <span data-e2e="video-title">搜索结果二</span>
            </div>
          </div>
          <a href="/video/{VIDEO_ID_3}" title="搜索区域外"></a>
        </body></html>
        """
        second_html = f"""
        <html><body>
          <div id="search-result-container">
            <div id="waterfall_item_{VIDEO_ID_2}" class="search-result-card"></div>
            <div class="search-result-card">
              <a href="//www.douyin.com/video/{VIDEO_ID_3}" title="动态新增结果"></a>
            </div>
          </div>
        </body></html>
        """

        extract_cards_from_html(first_html, self.ids, "search", self.video_data)
        extract_cards_from_html(second_html, self.ids, "search", self.video_data)

        self.assertEqual(self.ids, {VIDEO_ID_1, VIDEO_ID_2, VIDEO_ID_3})
        self.assertEqual(self.video_data[VIDEO_ID_3], "动态新增结果")

    def test_search_without_results_returns_empty(self):
        html = '<html><body><div id="search-result-container"></div></body></html>'

        extract_cards_from_html(html, self.ids, "search", self.video_data)

        self.assertEqual(self.ids, set())

    def test_json_response_extracts_id_keys_and_video_urls(self):
        payload = {
            "data": {
                "aweme_id": VIDEO_ID_1,
                "desc": "JSON 视频标题",
                "share_url": f"https://www.douyin.com/video/{VIDEO_ID_2}",
            },
            "unrelated": "7700000000000000099",
        }

        extract_ids_from_text(
            json.dumps(payload, ensure_ascii=False),
            self.ids,
            self.video_data,
        )

        self.assertEqual(self.ids, {VIDEO_ID_1, VIDEO_ID_2})
        self.assertEqual(self.video_data[VIDEO_ID_1], "JSON 视频标题")


class FakeMouse:
    def wheel(self, x, y):
        pass


class FakePage:
    def __init__(self, html):
        self.mouse = FakeMouse()
        self._html = html

    def wait_for_timeout(self, timeout):
        pass

    def content(self):
        return self._html


class FakeResponse:
    def __init__(self, url, payload):
        self.url = url
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")


class FakeNetwork:
    def __init__(self, responses):
        self._responses = responses

    def search(self, limit=None, include_static=False):
        return self._responses


class FakeSession:
    def __init__(self):
        self.page = FakePage("<html><body></body></html>")
        self.network = FakeNetwork(
            [
                FakeResponse(
                    "https://www.douyin.com/aweme/v1/web/tab/feed/",
                    {"aweme_list": [{"aweme_id": VIDEO_ID_1, "desc": "监听视频"}]},
                ),
                FakeResponse(
                    "https://www.douyin.com/aweme/v1/web/tab/feed/",
                    {"aweme_list": [{"aweme_id": VIDEO_ID_3}]},
                ),
                FakeResponse(
                    "https://www.douyin.com/aweme/v1/web/tab/feed/",
                    {"aweme_list": [{"aweme_id": VIDEO_ID_1, "desc": "监听视频"}]},
                ),
                FakeResponse(
                    "https://www.douyin.com/aweme/v1/web/user/profile/other/",
                    {"aweme_list": [{"aweme_id": VIDEO_ID_2, "desc": "无关响应"}]},
                ),
            ]
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def fetch(self, *args, **kwargs):
        page_action = kwargs.get("page_action")
        if page_action:
            page_action(self.page)
        return None


class RecommendationListenerTests(unittest.TestCase):
    def test_recommend_listener_keeps_feed_response_and_deduplicates(self):
        fake_session = FakeSession()
        with (
            patch(
                "backend.services.douyin_scraper.StealthySession",
                return_value=fake_session,
            ),
            patch(
                "backend.services.douyin_scraper.browser_profile_guard",
                side_effect=lambda *args, **kwargs: nullcontext(),
            ),
            patch(
                "backend.services.douyin_scraper.get_video_library_df",
                return_value=pd.DataFrame(columns=["视频ID", "标题"]),
            ),
            patch("backend.services.douyin_scraper.merge_video_library") as merge_mock,
        ):
            rows = next(
                fetch_ids_task_generator(
                    "https://www.douyin.com/?recommend=1",
                    "recommend",
                    lambda: True,
                )
            )

        self.assertEqual(
            rows,
            [
                {"视频ID": VIDEO_ID_1, "标题": "监听视频"},
                {"视频ID": VIDEO_ID_3, "标题": "无标题"},
            ],
        )
        merge_mock.assert_called_once_with(rows)


class VideoRepositoryIsolationTests(unittest.TestCase):
    def test_merge_persists_untitled_ids_and_updates_duplicate_title(self):
        with tempfile.TemporaryDirectory(prefix="video_repository_test_") as temp_dir:
            csv_path = Path(temp_dir) / "douyin_videos.csv"
            with patch.object(video_repository, "VIDEO_LIBRARY_PATH", csv_path):
                video_repository.merge_video_library(
                    [{"视频ID": VIDEO_ID_1, "标题": "初始标题"}]
                )
                video_repository.merge_video_library(
                    [
                        {"视频ID": VIDEO_ID_1, "标题": "更新标题"},
                        {"视频ID": VIDEO_ID_2, "标题": "无标题"},
                    ]
                )
                frame = video_repository.load_video_library_df(strict=True)

        self.assertEqual(len(frame), 2)
        self.assertEqual(
            frame.loc[frame["视频ID"] == VIDEO_ID_1, "标题"].iloc[0],
            "更新标题",
        )
        self.assertEqual(
            frame.loc[frame["视频ID"] == VIDEO_ID_2, "标题"].iloc[0],
            "无标题",
        )

    def test_isolated_repository_records_are_visible_through_video_apis(self):
        with tempfile.TemporaryDirectory(prefix="capture_api_isolation_") as temp_dir:
            csv_path = Path(temp_dir) / "douyin_videos.csv"
            queue_path = Path(temp_dir) / "download_queue.json"
            with patch.object(video_repository, "VIDEO_LIBRARY_PATH", csv_path):
                video_repository.merge_video_library(
                    [
                        {"视频ID": VIDEO_ID_1, "标题": "待下载视频"},
                        {"视频ID": VIDEO_ID_2, "标题": "已下载视频"},
                    ]
                )
                before_clear = file_sha256(csv_path)
                with (
                    patch(
                        "backend.api.main.acquire_backend_runtime_ownership",
                        fake_runtime_ownership,
                    ),
                    patch("backend.api.main.DEFAULT_QUEUE_STORE", queue_path),
                    patch(
                        "backend.services.video_query_service.get_video_library_df",
                        side_effect=lambda: video_repository.load_video_library_df(
                            strict=False
                        ),
                    ),
                    patch(
                        "backend.services.video_query_service.get_downloaded_ids",
                        return_value={VIDEO_ID_2},
                    ),
                    TestClient(app) as client,
                ):
                    videos = client.get("/api/videos", params={"page_size": 100})
                    summary = client.get("/api/videos/summary")
                    cleared = client.post("/api/capture/clear")
                after_clear = file_sha256(csv_path)

        self.assertEqual(videos.status_code, 200)
        self.assertEqual(videos.json()["data"]["total"], 2)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["data"]["total"], 2)
        self.assertEqual(summary.json()["data"]["downloaded_total"], 1)
        self.assertEqual(summary.json()["data"]["pending_total"], 1)
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(before_clear, after_clear)


if __name__ == "__main__":
    unittest.main()
