"""Read-only Windows process discovery shared by the launcher and update preflight."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as w
from dataclasses import dataclass

GAME_NAMES = {"amazing.exe", "gta_sa.exe"}


@dataclass(frozen=True)
class GameProcess:
    pid: int
    path: str
    created: str
    x86: bool
    error: str = ""

    @property
    def label(self):
        return f"PID {self.pid} · {self.path}" + (" · " + self.error if self.error else "")


def kernel_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel.GetProcessTimes.argtypes = [w.HANDLE, ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME)]
    kernel.IsWow64Process.argtypes = [w.HANDLE, ctypes.POINTER(w.BOOL)]
    return kernel


def process_info(pid: int) -> GameProcess:
    kernel = kernel_api()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        length = w.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            raise ctypes.WinError(ctypes.get_last_error())
        wow = w.BOOL()
        if not kernel.IsWow64Process(handle, ctypes.byref(wow)):
            raise ctypes.WinError(ctypes.get_last_error())
        created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        os64 = ctypes.sizeof(ctypes.c_void_p) == 8 or bool(os.getenv("PROCESSOR_ARCHITEW6432"))
        return GameProcess(pid, buffer.value, created, bool(wow.value) or not os64)
    finally:
        kernel.CloseHandle(handle)


def process_names():
    if sys.platform != "win32":
        return []
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("pid", w.DWORD),
                    ("heap", ctypes.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                    ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD), ("name", w.WCHAR * 260)]
    kernel = kernel_api()
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            result.append((entry.pid, entry.name))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(snapshot)
    return result


def find_games():
    result = []
    for pid, name in process_names():
        if name.casefold() in GAME_NAMES:
            try:
                game = process_info(pid)
                if os.path.basename(game.path).casefold() in GAME_NAMES:
                    result.append(game)
            except OSError as error:
                result.append(GameProcess(pid, name, "", False, str(error)))
    return sorted(result, key=lambda game: (game.path.casefold(), game.pid))
