"""External-window focus and native hotkeys, confined to the Qt GUI thread."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter


def parse_hotkey(sequence):
    parts = sequence.upper().replace(" ", "").split("+")
    key = parts.pop()
    mods = 0x4000  # MOD_NOREPEAT
    for name in parts:
        if name not in {"CTRL", "ALT", "SHIFT", "WIN"}:
            raise ValueError("Неизвестный модификатор горячей клавиши")
        mods |= {"CTRL": 2, "ALT": 1, "SHIFT": 4, "WIN": 8}[name]
    named = {"ESC": 27, "ESCAPE": 27, "UP": 38, "DOWN": 40, "LEFT": 37, "RIGHT": 39, "SPACE": 32}
    if key in named:
        vk = named[key]
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 111 + int(key[1:])
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key)
    else:
        raise ValueError("Используйте букву, цифру, F1–F24 или именованную клавишу")
    if not parts and vk != 27 and not 112 <= vk <= 135:
        raise ValueError("Для обычной клавиши укажите Ctrl/Alt/Shift")
    return mods, vk


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.GetPropW.restype = wintypes.HANDLE
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


def foreground():
    return int(user32.GetForegroundWindow() or 0) if sys.platform == "win32" else 0


def native_owner_window(hwnd):
    """Find the marked render window, including sibling windows of the same game."""
    if sys.platform != "win32" or not hwnd:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return 0
    def owned(window):
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(window_pid))
        return window_pid.value == pid.value and user32.GetPropW(window, "AmazingLawOverlay.NativeInputOwner.v1") == pid.value
    for window in {hwnd, user32.GetAncestor(hwnd, 2), user32.GetAncestor(hwnd, 3)}:
        if window and owned(window):
            return int(window)
    # Some game dialogs are separate top-level windows, rather than children or
    # owned popups. Native ownership belongs to the process, not just one HWND.
    found = []
    callback = user32.EnumWindows.argtypes[0](lambda window, _: (found.append(int(window)) or False) if owned(window) else True)
    user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def native_owns_input(hwnd):
    return bool(native_owner_window(hwnd))


def open_native_notebook(hwnd):
    """Forward an already queued Ctrl+N, without activating an external window."""
    window = native_owner_window(hwnd)
    if not window or not user32.GetPropW(window, "AmazingLawOverlay.NativeNotebookCommand.v1"):
        return False
    return bool(user32.PostMessageW(window, 0x8000 + 0x416, 1, 0))


def belongs_to_game(hwnd):
    if sys.platform != "win32" or not hwnd:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return False
    try:
        length = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(length.value)
        return bool(kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) and buffer.value.lower().endswith(("\\amazing.exe", "\\gta_sa.exe")))
    finally:
        kernel.CloseHandle(handle)


def set_input(hwnd, interactive):
    if sys.platform != "win32":
        return
    style = user32.GetWindowLongW(hwnd, -20)
    style |= 0x80000 | 0x80  # LAYERED | TOOLWINDOW
    style = style & ~(0x20 | 0x08000000) if interactive else style | 0x20 | 0x08000000
    ctypes.set_last_error(0)
    result = user32.SetWindowLongW(hwnd, -20, style)
    if not result and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    # TOPMOST without activation, movement, or resizing.
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x20)


def activate(hwnd):
    return bool(user32.IsWindow(hwnd) and user32.SetForegroundWindow(hwnd)) if sys.platform == "win32" else True


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, app, callback):
        super().__init__()
        self.app, self.callback = app, callback
        self.registered = []
        self.actions = {}
        self.desired = []
        self.active = False
        self.last_signature = None
        app.installNativeEventFilter(self)

    def configure(self, settings):
        parsed = [(2000, "toggle", parse_hotkey(settings["toggle_hotkey"])),
                  (2001, "input", parse_hotkey(settings["input_hotkey"])),
                  (2002, "escape", (0x4000, 27)),
                  (2003, "scroll_up", parse_hotkey(settings.get("scroll_up_hotkey", "Alt+Up"))),
                  (2004, "scroll_down", parse_hotkey(settings.get("scroll_down_hotkey", "Alt+Down"))),
                  (2005, "prev_law", parse_hotkey(settings.get("prev_law_hotkey", "Alt+Left"))),
                  (2006, "next_law", parse_hotkey(settings.get("next_law_hotkey", "Alt+Right"))),
                  (2007, "notes", parse_hotkey(settings.get("notes_hotkey", "Ctrl+N")))]
        if len({value for _, _, value in parsed}) != len(parsed):
            raise ValueError("Горячие клавиши совпадают")
        self.desired = parsed
        self.last_signature = None

    def set_active(self, enabled, visible=True):
        signature = (enabled, visible, tuple(self.desired))
        if self.last_signature == signature:
            return
        self.last_signature = signature
        self.clear()
        self.active = enabled
        if not enabled or sys.platform != "win32":
            return
        failures = []
        for ident, action, (mods, key) in self.desired:
            if not visible and action not in {"toggle", "input", "notes"}:
                continue
            if user32.RegisterHotKey(None, ident, mods, key):
                self.registered.append(ident)
                self.actions[ident] = action
            else:
                failures.append(action)
        if failures:
            raise ValueError("Не удалось зарегистрировать клавиши: " + ", ".join(failures) + ". Закройте старый оверлей или смените сочетания.")

    def clear(self):
        if sys.platform == "win32":
            for ident in self.registered:
                user32.UnregisterHotKey(None, ident)
        self.registered.clear()
        self.actions.clear()

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x312 and msg.wParam in self.actions:
                self.callback(self.actions[msg.wParam])
                return True, 0
        return False, 0
