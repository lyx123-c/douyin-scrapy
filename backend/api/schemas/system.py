from pydantic import BaseModel


class PrepareUpdateShutdownData(BaseModel):
    accepted: bool
    runtime_stopped: bool
    shutdown_scheduled: bool

class OpenSaveDirectoryData(BaseModel):
    path: str


class StorageRuntimePathsData(BaseModel):
    app_user_data_dir: str
    video_data_dir: str
    task_data_dir: str
    browser_profile_dir: str
    save_dir: str
    log_dir: str
    ffmpeg_path: str
    using_external_app_data: bool
