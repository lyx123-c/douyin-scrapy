from pydantic import BaseModel


class OpenSaveDirectoryData(BaseModel):
    path: str
