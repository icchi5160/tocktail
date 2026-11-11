import ctypes
from ctypes import wintypes as W
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

import pytest

from tocktail.browser.pipe import PipeClient, PipeServer
from tocktail.browser.bridge import BrowserBridge
from tocktail.browser.config import BridgeConfig
from tocktail.browser.matching import BrowserIdentity, NativeWindow, WindowEvidence
from tocktail.browser.native_host import relay, write_message
from tocktail.browser.report import domain_usage
from tocktail.config.settings import Settings
from tocktail.core.models import ActivityState, Application, Observation
from tocktail.runtime.service import TrackingService
from tocktail.storage.sqlite import Store


pytestmark = [pytest.mark.windows, pytest.mark.skipif(sys.platform != "win32", reason="Windows Named Pipes")]


def receive(server, predicate=lambda items: bool(items), timeout=3):
    items = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        items.extend(server.drain())
        if predicate(items):
            return items
        time.sleep(0.02)
    raise AssertionError("Timed out receiving Native Messaging pipe data")


def test_current_user_pipe_sends_receives_and_disconnects():
    server = PipeServer("\\\\.\\pipe\\Tocktail-test-" + uuid4().hex, slots=1)
    server.start()
    client = PipeClient(server.name)
    try:
        assert server.sddl == f"D:P(A;;GA;;;{server.api.evidence.user_sid()})"
        client.send(b'{"message":"hello"}')
        received = receive(server)[0]
        assert received.data == b'{"message":"hello"}'
        assert received.peer_pid == os.getpid()
        client.close()
        disconnected = receive(server)[0]
        assert disconnected.data is None and disconnected.connection == received.connection
    finally:
        client.close()
        server.close()
    assert not server._thread.is_alive()
    assert server.error is None


def test_concurrent_pipe_clients_and_first_instance_protection():
    server = PipeServer("\\\\.\\pipe\\Tocktail-test-" + uuid4().hex, slots=2)
    server.start()
    first, second = PipeClient(server.name), PipeClient(server.name)
    try:
        first.send(b"first")
        second.send(b"second")
        data = receive(server, lambda values: len(values) == 2)
        assert {item.data for item in data} == {b"first", b"second"}
        assert len({item.slot for item in data}) == 2
        with pytest.raises(OSError):
            PipeServer(server.name).start()
        with pytest.raises(ValueError):
            first.send(b"x" * 16385)
    finally:
        first.close()
        second.close()
        server.close()


def test_actual_pipe_dacl_grants_only_current_user():
    server = PipeServer("\\\\.\\pipe\\Tocktail-test-" + uuid4().hex, slots=1)
    server.start()
    try:
        # GetSecurityInfo(SE_FILE_OBJECT) reads the kernel object's actual DACL,
        # rather than merely checking the string used to create the pipe.
        api = server.api
        api.a.GetSecurityInfo.argtypes = [W.HANDLE, ctypes.c_int, W.DWORD,
            W.LPVOID, W.LPVOID, W.LPVOID, W.LPVOID, ctypes.POINTER(W.LPVOID)]
        api.a.GetSecurityInfo.restype = W.DWORD
        api.a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            W.LPVOID, W.DWORD, W.DWORD, ctypes.POINTER(W.LPWSTR), ctypes.POINTER(W.DWORD)]
        api.a.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = W.BOOL
        descriptor, text = W.LPVOID(), W.LPWSTR()
        result = api.a.GetSecurityInfo(server._slots[0].handle, 1, 4, None, None, None, None, ctypes.byref(descriptor))
        assert result == 0
        try:
            assert api.a.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text), None)
            # Windows canonicalizes GENERIC_ALL to FILE_ALL_ACCESS for pipe objects.
            assert text.value in {server.sddl, server.sddl.replace(";;GA;", ";;FA;")}
        finally:
            if text:
                api.k.LocalFree(ctypes.cast(text, W.LPVOID))
            api.k.LocalFree(descriptor)
    finally:
        server.close()


def test_native_framing_pipe_bridge_and_sqlite_pipeline(tmp_path):
    # Real OS pipes and framing, with a simulated browser ancestor/window proof.
    # This does not claim that a Chrome/Edge extension is installed in this test.
    base = 1_800_000_000_000
    extension_id = "a" * 32
    identity = BrowserIdentity("chrome", 20)
    config = BridgeConfig(tmp_path / "db", (extension_id,))
    server = PipeServer("\\\\.\\pipe\\Tocktail-test-" + uuid4().hex, slots=1)
    server.start()
    client = PipeClient(server.name)
    reader_fd, writer_fd = os.pipe()
    reader = os.fdopen(reader_fd, "rb", buffering=0)
    writer = os.fdopen(writer_fd, "wb", buffering=0)
    clock = {"now": base}
    failures = []

    def host():
        try:
            relay(reader, BytesIO(), origin=f"chrome-extension://{extension_id}/", config=config,
                  identity=identity, client=client, now_ms=lambda: clock["now"])
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=host, daemon=True)
    thread.start()

    class Inspector:
        def browser_ancestor(self, pid):
            assert pid == os.getpid()
            return identity

        def window_evidence(self, observation):
            return WindowEvidence(10, "Example - Google Chrome", (NativeWindow(10, 20),))

    bridge = BrowserBridge(config, server, Inspector())

    def transfer(sequence, stamp):
        clock["now"] = stamp
        write_message(writer, {"version": 1, "sequence": sequence, "timestamp_ms": stamp,
            "focused": True, "window_count": 1, "page": {"window_id": 777, "tab_id": 111,
            "navigation": "c" * 32, "origin": "https://example.com", "title": "Example",
            "media": {"detected": True, "playing": True, "age_ms": 0}}})
        items = receive(server)
        for item in items:
            server.mailbox.put(item)
        return items[-1].monotonic_ms

    def observed(utc, mono, idle=0):
        return Observation(utc, mono, Application("chrome.exe", "c:/chrome.exe"), 10, 20, idle_ms=idle)

    try:
        with Store(config.database) as store:
            service = TrackingService(store, own_pid=999)
            service.configure(Settings(idle_seconds=1))
            first = transfer(1, base)
            result, events = bridge.observe(observed(base, first), service.settings)
            service.observe(result, events)
            second = max(transfer(2, base + 1000), first + 300)
            result, events = bridge.observe(observed(base + 1000, second, 1000), service.settings)
            service.observe(result, events)
            assert service.engine.current.target.content.domain == "example.com"
            assert service.engine.current.target.state == ActivityState.PASSIVE
            result, events = bridge.observe(observed(base + 2000, second + 1000, 2000), service.settings)
            service.observe(result, events)
            writer.close()
            thread.join(3)
            assert not thread.is_alive()
            for item in receive(server):
                server.mailbox.put(item)
            result, events = bridge.observe(observed(base + 3000, second + 2000, 3000), service.settings)
            service.observe(result, events)
            assert service.engine.current.target.content is None
            assert service.engine.current.target.state == ActivityState.IDLE
            service.close()
            usage = domain_usage(store.connection, datetime.fromtimestamp(base / 1000).date())
            assert usage == (("example.com", 2000),)
            assert store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        writer.close()
        thread.join(3)
        reader.close()
        client.close()
        server.close()
    assert not failures
