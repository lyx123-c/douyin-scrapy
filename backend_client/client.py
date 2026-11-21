from typing import Any

import httpx

from config import (
    BACKEND_API_BASE_URL,
    BACKEND_API_CONNECT_TIMEOUT,
    BACKEND_API_LOGIN_TIMEOUT,
    BACKEND_API_READ_TIMEOUT,
)


class BackendClientError(RuntimeError):
    """Base error for Backend HTTP communication."""


class BackendUnavailableError(BackendClientError):
    pass


class BackendTimeoutError(BackendClientError):
    pass


class BackendHTTPError(BackendClientError):
    def __init__(self, status_code: int, message: str, code: str = "HTTP_ERROR") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class BackendResponseError(BackendClientError):
    pass


class BackendClient:
    def __init__(self, base_url: str = BACKEND_API_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            trust_env=False,
            timeout=httpx.Timeout(
                BACKEND_API_READ_TIMEOUT,
                connect=BACKEND_API_CONNECT_TIMEOUT,
            ),
        )

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method,
                path,
                json=json,
                params=params,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            raise BackendUnavailableError(
                f"无法连接 Backend：{self.base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise BackendTimeoutError("Backend 请求超时。") from exc
        except httpx.RequestError as exc:
            raise BackendUnavailableError(f"Backend 请求失败：{exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendResponseError("Backend 返回了无效 JSON。") from exc

        if not isinstance(payload, dict):
            raise BackendResponseError("Backend 响应结构不正确。")

        if response.status_code >= 400 or payload.get("success") is not True:
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            raise BackendHTTPError(
                response.status_code,
                str(payload.get("message") or "Backend 请求失败。"),
                str(data.get("code") or "HTTP_ERROR"),
            )

        return payload.get("data")

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def get_auth_status(self) -> dict:
        return self._request("GET", "/api/auth/status")

    def get_videos(
        self,
        *,
        keyword: str = "",
        status: str = "all",
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "library",
        sort_order: str = "asc",
    ) -> dict:
        return self._request(
            "GET",
            "/api/videos",
            params={
                "keyword": keyword,
                "status": status,
                "page": page,
                "page_size": page_size,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        )

    def get_video_summary(self) -> dict:
        return self._request("GET", "/api/videos/summary")

    def get_analytics(self, keyword: str = "") -> dict:
        return self._request(
            "GET",
            "/api/analytics",
            params={"keyword": keyword},
        )

    def capture_status(self) -> dict:
        return self._request("GET", "/api/capture/status")

    def start_capture(self, target_url: str, mode: str) -> dict:
        return self._request(
            "POST",
            "/api/capture/start",
            json={"target_url": target_url, "mode": mode},
        )

    def stop_capture(self) -> dict:
        return self._request("POST", "/api/capture/stop")

    def capture_results(self) -> dict:
        return self._request("GET", "/api/capture/results")

    def clear_capture(self) -> dict:
        return self._request("POST", "/api/capture/clear")

    def start_downloads(self) -> dict:
        return self._request("POST", "/api/downloads/start")

    def stop_downloads(self) -> dict:
        return self._request("POST", "/api/downloads/stop")

    def downloads_snapshot(self) -> dict:
        return self._request("GET", "/api/downloads")

    def downloads_status(self) -> dict:
        return self._request("GET", "/api/downloads/status")

    def queue_items(self, items: list[dict]) -> dict:
        return self._request("POST", "/api/downloads/tasks", json={"items": items})

    def immediate_download(self, video_id: str, title: str, force: bool = False) -> dict:
        return self._request(
            "POST",
            "/api/downloads/immediate",
            json={"video_id": video_id, "title": title, "force": force},
        )

    def retry_task(self, task_id: str) -> dict:
        return self._request("POST", f"/api/downloads/tasks/{task_id}/retry")

    def delete_task(self, task_id: str) -> dict:
        return self._request("DELETE", f"/api/downloads/tasks/{task_id}")

    def clear_completed(self) -> dict:
        return self._request("POST", "/api/downloads/tasks/clear-completed")

    def login(self) -> dict:
        return self._request(
            "POST",
            "/api/auth/login",
            timeout=BACKEND_API_LOGIN_TIMEOUT,
        )

    def open_save_directory(self) -> dict:
        return self._request("POST", "/api/system/open-save-directory")


def create_backend_client() -> BackendClient:
    return BackendClient()
