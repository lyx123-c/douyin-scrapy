import ctypes
import os
from contextlib import contextmanager

from config import BACKEND_RUNTIME_MUTEX_NAME


ERROR_ALREADY_EXISTS = 183


class RuntimeOwnershipError(RuntimeError):
    """Raised when another process already owns the Backend runtime."""


def _windows_mutex_functions():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    return kernel32


@contextmanager
def acquire_backend_runtime_ownership(name: str | None = None):
    """Hold an OS-level mutex for the lifetime of the Backend Runtime."""
    if os.name != "nt":
        raise RuntimeOwnershipError("当前 Backend Runtime 所有权保护仅支持 Windows。")

    mutex_name = name or BACKEND_RUNTIME_MUTEX_NAME
    kernel32 = _windows_mutex_functions()
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        raise RuntimeOwnershipError(
            f"无法创建 Backend Runtime Mutex，系统错误：{ctypes.get_last_error()}"
        )

    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        raise RuntimeOwnershipError("另一个 Backend Runtime 已经启动。")

    try:
        yield mutex_name
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)

