"""Hidden Win32 notification window, owned by the monitor's message-pump thread."""

import ctypes
from ctypes import wintypes as W
from collections.abc import Callable
from uuid import uuid4


class SessionInfo(ctypes.Structure):
    _fields_ = [("session", W.DWORD), ("state", ctypes.c_int), ("flags", W.LONG),
                ("station", W.WCHAR * 33), ("user", W.WCHAR * 21), ("domain", W.WCHAR * 18),
                ("times", ctypes.c_longlong * 5), ("counters", W.DWORD * 6)]


class ExtendedInfo(ctypes.Structure):
    _fields_ = [("level", W.DWORD), ("data", SessionInfo)]


class LifecycleWindow:
    def __init__(self, changed: Callable[[str, bool], None]) -> None:
        self.changed = changed
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.w = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [("style", W.UINT), ("proc", self.proc_type), ("class_extra", ctypes.c_int),
                        ("window_extra", ctypes.c_int), ("instance", W.HINSTANCE), ("icon", W.HICON),
                        ("cursor", W.HANDLE), ("brush", W.HBRUSH), ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]

        self.class_type = WindowClass
        self.u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        self.u.DefWindowProcW.restype = ctypes.c_ssize_t
        self.u.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        self.u.RegisterClassW.restype = W.WORD
        self.u.UnregisterClassW.argtypes = [W.LPCWSTR, W.HINSTANCE]
        self.u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
        self.u.CreateWindowExW.restype = W.HWND
        self.u.DestroyWindow.argtypes = [W.HWND]
        self.u.RegisterSuspendResumeNotification.argtypes = [W.HANDLE, W.DWORD]
        self.u.RegisterSuspendResumeNotification.restype = W.HANDLE
        self.u.UnregisterSuspendResumeNotification.argtypes = [W.HANDLE]
        self.w.WTSRegisterSessionNotification.argtypes = [W.HWND, W.DWORD]
        self.w.WTSUnRegisterSessionNotification.argtypes = [W.HWND]
        self.w.WTSQuerySessionInformationW.argtypes = [W.HANDLE, W.DWORD, ctypes.c_int,
                                                       ctypes.POINTER(W.LPVOID), ctypes.POINTER(W.DWORD)]
        self.w.WTSFreeMemory.argtypes = [W.LPVOID]
        self.k.GetModuleHandleW.argtypes = [W.LPCWSTR]
        self.k.GetModuleHandleW.restype = W.HMODULE
        self.name = "TocktailLifecycle-" + uuid4().hex
        self.instance = self.k.GetModuleHandleW(None)
        self.callback = self.proc_type(self._message)
        self.hwnd = None
        self.power = None
        self.registered = False
        self.session_registered = False

    def locked(self) -> bool:
        buffer, size = W.LPVOID(), W.DWORD()
        # WTS_CURRENT_SESSION. Names and timestamps in this temporary buffer are
        # never read or persisted; only connection state and lock flags are used.
        if not self.w.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 25, ctypes.byref(buffer), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if size.value < ctypes.sizeof(ExtendedInfo):
                raise RuntimeError("Incomplete Windows session state")
            info = ctypes.cast(buffer, ctypes.POINTER(ExtendedInfo)).contents
            if info.level != 1:
                raise RuntimeError("Unknown Windows session information level")
            return info.data.state != 0 or info.data.flags != 1
        finally:
            self.w.WTSFreeMemory(buffer)

    def start(self) -> None:
        wc = self.class_type()
        wc.proc, wc.instance, wc.name = self.callback, self.instance, self.name
        if not self.u.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.registered = True
        try:
            # Hidden top-level window receives power broadcasts; no WS_VISIBLE.
            self.hwnd = self.u.CreateWindowExW(0, self.name, "Tocktail lifecycle", 0,
                                               0, 0, 0, 0, None, None, self.instance, None)
            if not self.hwnd or not self.w.WTSRegisterSessionNotification(self.hwnd, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            self.session_registered = True
            self.power = self.u.RegisterSuspendResumeNotification(self.hwnd, 0)
            if not self.power:
                raise ctypes.WinError(ctypes.get_last_error())
            self.changed("locked", self.locked())
        except BaseException:
            self.close()
            raise

    def _message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == 0x0218:  # WM_POWERBROADCAST
            if wparam == 4:
                self.changed("suspended", True)
            elif wparam in (6, 7, 18):
                self.changed("suspended", False)
            return 1
        if message == 0x02B1:  # WM_WTSSESSION_CHANGE, current session only.
            if wparam in (2, 4, 7):  # Console/remote disconnect, lock.
                self.changed("locked", True)
            elif wparam in (1, 3, 8):  # Connect/unlock: refresh rather than assume unlocked.
                try:
                    self.changed("locked", self.locked())
                except (OSError, RuntimeError):
                    self.changed("locked", True)
            return 0
        return self.u.DefWindowProcW(hwnd, message, wparam, lparam)

    def close(self) -> None:
        if self.power:
            self.u.UnregisterSuspendResumeNotification(self.power)
            self.power = None
        if self.session_registered:
            self.w.WTSUnRegisterSessionNotification(self.hwnd)
            self.session_registered = False
        if self.hwnd:
            self.u.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.registered:
            self.u.UnregisterClassW(self.name, self.instance)
            self.registered = False
