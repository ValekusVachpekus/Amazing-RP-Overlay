"""Distribution identity; mutable user data never belongs in Program Files."""
from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from pathlib import Path

RUNTIME_MUTEX = "Global\\AmazingHelper.Runtime.v1"


def installed(root: Path) -> bool:
    marker = root / "installed.json"
    return marker.is_file() and json.loads(marker.read_text(encoding="utf-8")) == {"distribution": "installed"}


def documents() -> Path:
    if sys.platform != "win32":
        return Path.home() / "Documents"
    import uuid
    guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("fdd39ad0-238f-46af-adb4-6c85480369c7").bytes_le)
    pointer = ctypes.c_void_p()
    shell = ctypes.WinDLL("shell32")
    result = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer))
    if result:
        raise OSError("Не удалось определить папку Документы")
    try:
        return Path(ctypes.wstring_at(pointer))
    finally:
        ctypes.OleDLL("ole32").CoTaskMemFree(pointer)


def default_notes(root: Path) -> Path:
    return documents() / "AmazingHelper" / "Notes" if installed(root) else root / "Notes"


def runtime_guard():
    """Keep one shared kernel object alive while any application mode is running."""
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    from ctypes import wintypes as w
    class Security(ctypes.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]
    security_api = ctypes.WinDLL("advapi32", use_last_error=True)
    security_api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    # All users can hold a synchronize-only handle; admins can inspect it.
    # Default mutex ACLs would prevent a second Windows user from launching.
    acl = "D:(A;;0x00100000;;;AU)(A;;GA;;;SY)(A;;GA;;;BA)"
    if not security_api.ConvertStringSecurityDescriptorToSecurityDescriptorW(acl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        security = Security(ctypes.sizeof(Security), descriptor, False)
        kernel.CreateMutexExW.argtypes = [ctypes.POINTER(Security), w.LPCWSTR, w.DWORD, w.DWORD]
        kernel.CreateMutexExW.restype = w.HANDLE
        handle = kernel.CreateMutexExW(ctypes.byref(security), RUNTIME_MUTEX, 0, 0x100000)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle  # Held for process lifetime; installer never kills it.
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(descriptor)


def verify_bundle(root: Path, names):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name in names:
        path = root / name
        entry = manifest["files"][name]
        if path.is_symlink() or path.stat().st_size != entry["bytes"]:
            raise ValueError("Повреждён файл комплекта: " + name)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError("Не совпала контрольная сумма: " + name)
