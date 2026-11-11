"""ctypes Win32 adapter. Hook callback only signals; worker samples and writes."""

import ctypes
from dataclasses import replace
from ctypes import wintypes
import os
from pathlib import PureWindowsPath
from queue import Empty, Full, Queue
import sys
import threading
import time

from tocktail.core.models import Application, Observation


def idle_milliseconds(tick_ms: int, last_input_tick: int) -> int:
    """GetLastInputInfo contains a wrapping DWORD, unlike GetTickCount64."""
    return ((tick_ms & 0xFFFFFFFF) - last_input_tick) & 0xFFFFFFFF


class WindowsMonitor:
    def __init__(self, *, save_titles: bool = False) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Live monitoring requires Windows 11")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.save_titles = save_titles
        self._signals: Queue[None] = Queue(maxsize=1)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._locked = self._suspended = False
        self._transitions: Queue[Observation] = Queue(maxsize=64)
        self.lifecycle = None
        self._bind()

    def _bind(self) -> None:
        self.callback_type = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
            wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
        )
        u, k = self.user32, self.kernel32
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                     self.callback_type, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        u.SetWinEventHook.restype = wintypes.HANDLE
        u.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        u.UnhookWinEvent.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                  wintypes.UINT, wintypes.UINT, wintypes.UINT]
        u.PeekMessageW.restype = wintypes.BOOL
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.TranslateMessage.restype = wintypes.BOOL
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        k.GetTickCount64.argtypes = []
        k.GetTickCount64.restype = ctypes.c_ulonglong
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL

        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        self._input_type = LastInputInfo
        u.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInputInfo)]
        u.GetLastInputInfo.restype = wintypes.BOOL

    def _notify(self, *args: object) -> None:
        try:
            self._signals.put_nowait(None)
        except Full:
            pass  # Signals are coalesced; the worker always reads fresh OS state.

    def start(self) -> None:
        self._thread = threading.Thread(target=self._hook_loop, name="win-event-hook", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self.close()
            raise RuntimeError("WinEvent hook startup timed out")
        if self._error:
            raise RuntimeError("Could not register WinEvent hooks") from self._error

    def _hook_loop(self) -> None:
        from tocktail.monitors.lifecycle import LifecycleWindow
        hooks: list[int] = []
        callback = self.callback_type(self._notify)  # Strong reference until unhooked.
        try:
            self.lifecycle = LifecycleWindow(self._lifecycle_changed)
            self.lifecycle.start()
            for event in (0x0003, 0x800C):  # FOREGROUND and OBJECT_NAMECHANGE.
                hook = self.user32.SetWinEventHook(event, event, None, callback, 0, 0, 0)
                if not hook:
                    raise ctypes.WinError(ctypes.get_last_error())
                hooks.append(hook)
            self._ready.set()
            message = wintypes.MSG()
            while not self._stop.is_set():
                while self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    self.user32.TranslateMessage(ctypes.byref(message))
                    self.user32.DispatchMessageW(ctypes.byref(message))
                self._stop.wait(0.01)
        except BaseException as error:
            self._error = error
            self._ready.set()
        finally:
            for hook in hooks:
                self.user32.UnhookWinEvent(hook)
            if self.lifecycle is not None:
                self.lifecycle.close()

    def _lifecycle_changed(self, field: str, value: bool) -> None:
        with self._state_lock:
            attribute = "_" + field
            if getattr(self, attribute) == value:
                return
            setattr(self, attribute, value)
            try:
                self._transitions.put_nowait(Observation(
                    time.time_ns() // 1_000_000, time.monotonic_ns() // 1_000_000,
                    locked=self._locked, suspended=self._suspended, source="windows_lifecycle"))
            except Full:
                self._error = RuntimeError("Windows lifecycle event queue overflow; tracking stopped")
            self._notify()

    def observations(self, source: str = "poll") -> tuple[Observation, ...]:
        """Ordered transitions and fresh state, without racing the callback queue."""
        with self._state_lock:
            if self._error:
                raise RuntimeError("Windows monitor failed") from self._error
            transitions = []
            while not self._transitions.empty():
                transitions.append(self._transitions.get_nowait())
            return (*transitions, replace(self.sample(source), locked=self._locked, suspended=self._suspended))

    def wait(self, timeout: float) -> bool:
        """Return True on an OS signal, False on the polling deadline."""
        if self._error:
            raise RuntimeError("WinEvent hook failed") from self._error
        try:
            self._signals.get(timeout=max(0, timeout))
            return True
        except Empty:
            return False

    def sample(self, source: str = "poll") -> Observation:
        hwnd = self.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        app = None
        title = ""
        if hwnd and self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            handle = self.kernel32.OpenProcess(0x1000, False, pid.value)
            if handle:
                try:
                    buffer = ctypes.create_unicode_buffer(32768)
                    length = wintypes.DWORD(len(buffer))
                    if self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                        path = buffer.value.casefold()
                        app = Application(PureWindowsPath(path).name, path)
                finally:
                    self.kernel32.CloseHandle(handle)
            if self.save_titles and pid.value != os.getpid():
                buffer = ctypes.create_unicode_buffer(4096)
                self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
                title = buffer.value
        info = self._input_type()
        info.cbSize = ctypes.sizeof(info)
        if not self.user32.GetLastInputInfo(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Discard a torn observation if the foreground changed during lookup.
        if hwnd != self.user32.GetForegroundWindow():
            app, title = None, ""
        return Observation(
            utc_ms=time.time_ns() // 1_000_000,
            monotonic_ms=time.monotonic_ns() // 1_000_000,
            application=app, hwnd=hwnd, pid=pid.value, title=title,
            idle_ms=idle_milliseconds(self.kernel32.GetTickCount64(), info.dwTime),
            locked=self._locked, suspended=self._suspended,
            excluded=pid.value == os.getpid(), source=source,
        )

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
