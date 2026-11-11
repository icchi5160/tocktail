"""Local-only, current-user-only Windows Named Pipes with bounded overlapped I/O."""

import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass
import threading
import time
from uuid import uuid4

from tocktail.browser.protocol import MAX_MESSAGE
from tocktail.browser.windows import INVALID_HANDLE, WindowsEvidence


class Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", W.DWORD), ("OffsetHigh", W.DWORD), ("hEvent", W.HANDLE)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", W.LPVOID), ("inherit", W.BOOL)]


@dataclass(frozen=True)
class Received:
    slot: int
    connection: str
    peer_pid: int
    data: bytes | None
    monotonic_ms: int


class Mailbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[int, Received] = {}

    def put(self, item: Received) -> None:
        with self._lock:
            self._latest[item.slot] = item

    def drain(self) -> tuple[Received, ...]:
        with self._lock:
            values = tuple(self._latest.values())
            self._latest.clear()
            return values


class PipeAPI:
    def __init__(self) -> None:
        self.evidence = WindowsEvidence()
        self.k, self.a = self.evidence.k, self.evidence.a
        specs = {
            "CreateNamedPipeW": ([W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                                   ctypes.POINTER(SecurityAttributes)], W.HANDLE),
            "CreateFileW": ([W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            "CreateEventW": ([W.LPVOID, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE),
            "ResetEvent": ([W.HANDLE], W.BOOL),
            "ConnectNamedPipe": ([W.HANDLE, ctypes.POINTER(Overlapped)], W.BOOL),
            "DisconnectNamedPipe": ([W.HANDLE], W.BOOL),
            "GetNamedPipeClientProcessId": ([W.HANDLE, ctypes.POINTER(W.ULONG)], W.BOOL),
            "GetOverlappedResult": ([W.HANDLE, ctypes.POINTER(Overlapped), ctypes.POINTER(W.DWORD), W.BOOL], W.BOOL),
            "CancelIoEx": ([W.HANDLE, ctypes.POINTER(Overlapped)], W.BOOL),
            "WaitForSingleObject": ([W.HANDLE, W.DWORD], W.DWORD),
            "WaitNamedPipeW": ([W.LPCWSTR, W.DWORD], W.BOOL),
        }
        for name in ("ReadFile", "WriteFile"):
            specs[name] = ([W.HANDLE, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.POINTER(Overlapped)], W.BOOL)
        for name, (arguments, result) in specs.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = arguments, result
        self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            W.LPCWSTR, W.DWORD, ctypes.POINTER(W.LPVOID), ctypes.POINTER(W.DWORD)]
        self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = W.BOOL

    def event(self) -> Overlapped:
        event = self.k.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        return Overlapped(hEvent=event)

    def dispose(self, handle: int, operation: Overlapped) -> None:
        self.k.CancelIoEx(handle, None)
        self.k.WaitForSingleObject(operation.hEvent, 1000)
        self.k.CloseHandle(handle)
        self.k.CloseHandle(operation.hEvent)


class PipeSlot:
    def __init__(self, api: PipeAPI, handle: int, index: int, mailbox: Mailbox) -> None:
        self.api, self.handle, self.index, self.mailbox = api, handle, index, mailbox
        self.operation = api.event()
        self.buffer = ctypes.create_string_buffer(MAX_MESSAGE)
        self.state = "connect"
        self.connection = ""
        self.peer_pid = 0
        self.pending = False
        self.bytes_read = 0
        self.connect()

    def connect(self) -> None:
        self.state = "connect"
        self.api.k.ResetEvent(self.operation.hEvent)
        if self.api.k.ConnectNamedPipe(self.handle, ctypes.byref(self.operation)):
            self.pending = False
        else:
            code = ctypes.get_last_error()
            if code not in {535, 997}:  # PIPE_CONNECTED / IO_PENDING
                raise ctypes.WinError(code)
            self.pending = code == 997

    def disconnect(self) -> None:
        if self.connection:
            self.mailbox.put(Received(self.index, self.connection, self.peer_pid, None, time.monotonic_ns() // 1_000_000))
        self.connection = ""
        self.api.k.DisconnectNamedPipe(self.handle)
        self.connect()

    def read(self) -> None:
        self.state = "read"
        self.api.k.ResetEvent(self.operation.hEvent)
        count = W.DWORD()
        success = self.api.k.ReadFile(self.handle, self.buffer, MAX_MESSAGE, ctypes.byref(count), ctypes.byref(self.operation))
        self.bytes_read = count.value
        if success:
            self.pending = False
        elif ctypes.get_last_error() == 997:
            self.pending = True
        else:
            self.disconnect()

    def step(self) -> None:
        if self.pending:
            if self.api.k.WaitForSingleObject(self.operation.hEvent, 0) != 0:
                return
            count = W.DWORD()
            if not self.api.k.GetOverlappedResult(self.handle, ctypes.byref(self.operation), ctypes.byref(count), False):
                self.disconnect()
                return
            self.bytes_read = count.value
            self.pending = False
        if self.state == "connect":
            pid = W.ULONG()
            if not self.api.k.GetNamedPipeClientProcessId(self.handle, ctypes.byref(pid)):
                self.disconnect()
                return
            self.connection, self.peer_pid = uuid4().hex, pid.value
            self.read()
        elif self.bytes_read:
            self.mailbox.put(Received(self.index, self.connection, self.peer_pid,
                                     self.buffer.raw[:self.bytes_read], time.monotonic_ns() // 1_000_000))
            self.read()
        else:
            self.disconnect()


class PipeServer:
    """Eight slots, one pending read per slot, one latest update per slot."""

    def __init__(self, name: str, *, slots: int = 8) -> None:
        if not 1 <= slots <= 8:
            raise ValueError("Pipe slot count must be 1–8")
        self.name, self.slot_count = name, slots
        self.mailbox = Mailbox()
        self._stop = threading.Event()
        self._slots: list[PipeSlot] = []
        self._thread: threading.Thread | None = None
        self.error: OSError | None = None

    def start(self) -> None:
        self.api = PipeAPI()
        descriptor = W.LPVOID()
        sid = self.api.evidence.user_sid()
        self.sddl = f"D:P(A;;GA;;;{sid})"
        if not self.api.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(self.sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        try:
            for index in range(self.slot_count):
                # INBOUND | OVERLAPPED | FIRST_PIPE_INSTANCE; message mode; reject network clients.
                flags = 1 | 0x40000000 | (0x80000 if index == 0 else 0)
                handle = self.api.k.CreateNamedPipeW(self.name, flags, 4 | 2 | 8, self.slot_count,
                                                     MAX_MESSAGE, MAX_MESSAGE, 0, ctypes.byref(attributes))
                if handle == INVALID_HANDLE:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    self._slots.append(PipeSlot(self.api, handle, index, self.mailbox))
                except BaseException:
                    self.api.k.CloseHandle(handle)
                    raise
        except BaseException:
            for slot in self._slots:
                self.api.dispose(slot.handle, slot.operation)
            self._slots.clear()
            raise
        finally:
            self.api.k.LocalFree(descriptor)
        self._thread = threading.Thread(target=self._run, name="browser-pipe", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                for slot in self._slots:
                    slot.step()
                self._stop.wait(0.02)
        except OSError as error:
            self.error = error
        finally:
            for slot in self._slots:
                self.api.dispose(slot.handle, slot.operation)

    def drain(self) -> tuple[Received, ...]:
        if self.error:
            raise self.error
        return self.mailbox.drain()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
            if self._thread.is_alive():
                raise RuntimeError("Browser pipe did not stop")


class PipeClient:
    def __init__(self, name: str) -> None:
        self.name, self.api = name, PipeAPI()
        self.handle: int | None = None

    def send(self, data: bytes) -> None:
        if not 0 < len(data) <= MAX_MESSAGE:
            raise ValueError("Invalid pipe message size")
        if self.handle is None:
            handle = self.api.k.CreateFileW(self.name, 0x40000000, 0, None, 3, 0x40000000, None)
            if handle == INVALID_HANDLE:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = handle
        operation = self.api.event()
        buffer = ctypes.create_string_buffer(data)
        count = W.DWORD()
        try:
            success = self.api.k.WriteFile(self.handle, buffer, len(data), ctypes.byref(count), ctypes.byref(operation))
            if not success:
                code = ctypes.get_last_error()
                if code != 997:
                    raise ctypes.WinError(code)
                if self.api.k.WaitForSingleObject(operation.hEvent, 500) != 0:
                    self.api.k.CancelIoEx(self.handle, ctypes.byref(operation))
                    self.api.k.GetOverlappedResult(self.handle, ctypes.byref(operation), ctypes.byref(count), True)
                    raise TimeoutError("Browser pipe write timed out")
                if not self.api.k.GetOverlappedResult(self.handle, ctypes.byref(operation), ctypes.byref(count), False):
                    raise ctypes.WinError(ctypes.get_last_error())
            if count.value != len(data):
                raise OSError("Partial browser pipe message")
        except OSError:
            self.close()
            raise
        finally:
            self.api.k.CloseHandle(operation.hEvent)

    def close(self) -> None:
        if self.handle is not None:
            self.api.k.CloseHandle(self.handle)
            self.handle = None
