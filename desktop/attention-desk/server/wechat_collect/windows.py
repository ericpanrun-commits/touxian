"""Permission-gated Win32 reads only. No keyboard, clipboard or mouse injection."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DesktopState:
    idle_seconds: float
    unlocked: bool
    foreground: int
    mouse: tuple[int, int]


def desktop_state(policy, override=None):
    policy.require(operation='desktop_state', override=override)
    if os.name != 'nt':
        raise RuntimeError('Windows interactive session required')
    import ctypes
    from ctypes import wintypes
    # Initialise only this collector process before the first coordinate sample.
    # The pinned UIA library does the same on import; a 150% desktop otherwise
    # changes both cursor APIs' units across import without any physical input.
    dpi=ctypes.windll.shcore.SetProcessDpiAwareness(2)
    if dpi not in (0,-2147024891):  # S_OK or awareness already set by the process manifest.
        raise RuntimeError('DPI coordinate context unavailable')
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    class LastInput(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]
    value = LastInput(ctypes.sizeof(LastInput), 0)
    if not user32.GetLastInputInfo(ctypes.byref(value)):
        raise RuntimeError('Idle state unavailable')
    idle = ((kernel32.GetTickCount() & 0xffffffff) - value.dwTime) & 0xffffffff
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    handle = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS only.
    desktop_name = ctypes.create_unicode_buffer(256)
    required = wintypes.DWORD()
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    unlocked = bool(handle and user32.GetUserObjectInformationW(handle,2,desktop_name,ctypes.sizeof(desktop_name),ctypes.byref(required)) and desktop_name.value=='Default')
    if handle:
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop(handle)
    point = wintypes.POINT()
    # uiautomation initialises per-monitor DPI awareness on import. Logical
    # GetCursorPos coordinates can change without physical input across import.
    user32.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetPhysicalCursorPos.restype = wintypes.BOOL
    if not user32.GetPhysicalCursorPos(ctypes.byref(point)):
        raise RuntimeError('Physical cursor state unavailable')
    user32.GetForegroundWindow.restype = wintypes.HWND
    return DesktopState(idle / 1000, unlocked, int(user32.GetForegroundWindow() or 0), (point.x, point.y))


def require_idle(policy, override=None):
    state = desktop_state(policy, override)
    if not state.unlocked or state.idle_seconds < policy.config.idle_seconds:
        from ..personal_store import PersonalError
        raise PersonalError('等待解锁和至少 120 秒键鼠空闲。', 'waiting_idle', 409)
    return state
