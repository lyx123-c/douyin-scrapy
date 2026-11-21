from pydantic import BaseModel


class AuthStatusData(BaseModel):
    login_ready: bool


class AuthLoginData(BaseModel):
    profile_saved: bool
