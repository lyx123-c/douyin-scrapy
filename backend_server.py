import multiprocessing
import os
import sys
from pathlib import Path


def _runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _configure_playwright_browsers() -> Path | None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])

    browser_root = _runtime_base_dir() / "playwright-browsers"
    if browser_root.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
        return browser_root
    return None

def _configure_ffmpeg() -> None:
    if not getattr(sys, "frozen", False) or os.environ.get("FFMPEG_PATH"):
        return

    os.environ["FFMPEG_PATH"] = str(
        _runtime_base_dir().parent / "ffmpeg" / "ffmpeg.exe"
    )

def _configure_user_data() -> None:
    if not getattr(sys, "frozen", False) or os.environ.get("DOUYIN_APP_USER_DATA_DIR"):
        return

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    os.environ["DOUYIN_APP_USER_DATA_DIR"] = str(
        Path(local_app_data) / "DouyinVideoWorkbench"
    )

def _run_browser_smoke() -> int:
    from patchright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--headless=new", "--disable-gpu"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><head><title>Backend Browser OK</title></head></html>")
            print(f"BROWSER_SMOKE_TITLE={page.title()}", flush=True)
        finally:
            browser.close()
    return 0


def main() -> int:
    multiprocessing.freeze_support()
    _configure_user_data()
    _configure_ffmpeg()
    browser_root = _configure_playwright_browsers()

    if "--browser-smoke" in sys.argv:
        print(f"PLAYWRIGHT_BROWSERS_PATH={browser_root}", flush=True)
        return _run_browser_smoke()

    from backend.api.main import app
    import uvicorn

    host = os.environ.get("DOUYIN_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("DOUYIN_BACKEND_PORT", "8000"))
    log_level = os.environ.get("DOUYIN_BACKEND_LOG_LEVEL", "info")
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
    )
    server = uvicorn.Server(config)
    app.state.request_process_shutdown = lambda: setattr(server, "should_exit", True)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
