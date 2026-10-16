from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
from types import TracebackType


class AlreadyRunningError(RuntimeError):
    """Raised when another process already owns the same runtime role."""


class InstanceLock:
    """Process-lifetime role lock that is released automatically after a crash."""

    def __init__(self, role: str, scope: Path) -> None:
        digest = hashlib.sha256(str(scope.resolve()).casefold().encode("utf-8")).hexdigest()[:16]
        safe_role = "".join(character for character in role if character.isalnum()) or "process"
        self.name = f"VRFlorianStoryPlatform_{safe_role}_{digest}"
        self.scope = scope
        self._handle: int | None = None
        self._file = None

    def acquire(self) -> None:
        if self._handle is not None or self._file is not None:
            return
        if os.name == "nt":
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, False, "Local\\" + self.name)
            if not handle:
                raise OSError(ctypes.get_last_error(), "Windows could not create the instance guard")
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                raise AlreadyRunningError(self.name)
            self._handle = int(handle)
            return

        import fcntl

        lock_dir = self.scope / "runtime" / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = (lock_dir / f"{self.name}.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise AlreadyRunningError(self.name) from exc
        self._file = lock_file

    def release(self) -> None:
        if self._handle is not None:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = None
        if self._file is not None:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

