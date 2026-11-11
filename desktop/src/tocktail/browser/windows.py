"""Windows-only evidence acquisition. No browser IDs are translated to HWNDs."""

import ctypes
from ctypes import wintypes as W
import os
from pathlib import PureWindowsPath
import sys

from tocktail.browser.matching import BrowserIdentity, NativeWindow, WindowEvidence, browser_root
from tocktail.core.models import Observation


INVALID_HANDLE = ctypes.c_void_p(-1).value


class ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("pid", W.DWORD),
                ("heap", ctypes.c_size_t), ("module", W.DWORD), ("threads", W.DWORD),
                ("parent", W.DWORD), ("priority", W.LONG), ("flags", W.DWORD), ("exe", W.WCHAR * 260)]


class WindowsEvidence:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Browser integration requires Windows")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self.k.GetCurrentProcess.restype = W.HANDLE
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = W.HANDLE
        for name in ("Process32FirstW", "Process32NextW"):
            function = getattr(self.k, name)
            function.argtypes = [W.HANDLE, ctypes.POINTER(ProcessEntry)]
            function.restype = W.BOOL
        self.k.LocalFree.argtypes = [W.HLOCAL]
        self.k.LocalFree.restype = W.HLOCAL
        self.a.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
        self.a.OpenProcessToken.restype = W.BOOL
        self.a.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD)]
        self.a.GetTokenInformation.restype = W.BOOL
        self.a.ConvertSidToStringSidW.argtypes = [W.LPVOID, ctypes.POINTER(W.LPWSTR)]
        self.a.ConvertSidToStringSidW.restype = W.BOOL
        self.u.GetForegroundWindow.restype = W.HWND
        self.u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        self.u.GetWindowThreadProcessId.restype = W.DWORD
        self.u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        self.u.GetWindowTextW.restype = ctypes.c_int
        self.u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        self.u.GetClassNameW.restype = ctypes.c_int
        self.u.IsWindowVisible.argtypes = [W.HWND]
        self.u.IsWindowVisible.restype = W.BOOL
        self.enum_callback = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.u.EnumWindows.argtypes = [self.enum_callback, W.LPARAM]
        self.u.EnumWindows.restype = W.BOOL

    def user_sid(self) -> str:
        token = W.HANDLE()
        if not self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = W.DWORD()
            self.a.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not self.a.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(buffer, ctypes.POINTER(W.LPVOID))[0]
            text = W.LPWSTR()
            if not self.a.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return text.value
            finally:
                self.k.LocalFree(ctypes.cast(text, W.LPVOID))
        finally:
            self.k.CloseHandle(token)

    def process_name(self, pid: int) -> str | None:
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            text = ctypes.create_unicode_buffer(32768)
            size = W.DWORD(len(text))
            if self.k.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)):
                return PureWindowsPath(text.value).name.casefold()
            return None
        finally:
            self.k.CloseHandle(handle)

    def browser_ancestor(self, pid: int | None = None) -> BrowserIdentity | None:
        pid = os.getpid() if pid is None else pid
        handle = self.k.CreateToolhelp32Snapshot(2, 0)
        if handle == INVALID_HANDLE:
            return None
        parents: dict[int, int] = {}
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            valid = self.k.Process32FirstW(handle, ctypes.byref(entry))
            while valid:
                parents[entry.pid] = entry.parent
                valid = self.k.Process32NextW(handle, ctypes.byref(entry))
        finally:
            self.k.CloseHandle(handle)
        return browser_root(pid, parents, self.process_name)

    def title(self, hwnd: int) -> str:
        text = ctypes.create_unicode_buffer(4096)
        self.u.GetWindowTextW(hwnd, text, len(text))
        return text.value

    def window_evidence(self, observation: Observation) -> WindowEvidence:
        foreground = self.u.GetForegroundWindow()
        if not observation.hwnd or foreground != observation.hwnd or not observation.application:
            return WindowEvidence(foreground, "", (), False)
        title = self.title(foreground)
        windows: list[NativeWindow] = []
        complete = True
        process = observation.application.process_name.casefold()

        def visit(hwnd, _):
            nonlocal complete
            if not self.u.IsWindowVisible(hwnd):
                return True
            name = ctypes.create_unicode_buffer(256)
            if not self.u.GetClassNameW(hwnd, name, len(name)):
                complete = False
                return True
            if name.value.startswith("Chrome_WidgetWin_"):
                pid = W.DWORD()
                self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                executable = self.process_name(pid.value)
                if executable is None:
                    complete = False
                elif executable == process:
                    windows.append(NativeWindow(hwnd, pid.value, self.title(hwnd)))
            return True

        if not self.u.EnumWindows(self.enum_callback(visit), 0):
            complete = False
        # Reject changes during inventory acquisition, including a background
        # window navigating to the same title as the foreground candidate.
        for window in windows:
            check_pid = W.DWORD()
            self.u.GetWindowThreadProcessId(window.hwnd, ctypes.byref(check_pid))
            if (not self.u.IsWindowVisible(window.hwnd) or check_pid.value != window.pid
                    or self.title(window.hwnd) != window.title):
                complete = False
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        complete = (complete and self.u.GetForegroundWindow() == foreground
                    and self.title(foreground) == title and pid.value == observation.pid)
        return WindowEvidence(foreground, title, tuple(windows), complete)
