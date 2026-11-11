from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tocktail.monitors.windows import WindowsMonitor, idle_milliseconds


def test_last_input_tick_wrap():
    assert idle_milliseconds(0x100000020, 0xFFFFFFF0) == 48
    assert idle_milliseconds(1500, 1000) == 500


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="requires a live Windows desktop")
def test_live_foreground_and_input():
    monitor = WindowsMonitor()
    if not monitor.user32.GetForegroundWindow():
        pytest.skip("No accessible Windows foreground window; use an interactive unlocked desktop")
    sample = monitor.sample()
    assert sample.monotonic_ms > 0
    assert sample.utc_ms > 0
    assert sample.idle_ms >= 0
    assert sample.hwnd
    assert sample.pid
    assert sample.application is not None
    assert not sample.title


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="requires a live Windows desktop")
def test_real_hook_receives_event_and_shuts_down():
    monitor = WindowsMonitor()
    monitor.start()
    try:
        while monitor.wait(0):
            pass
        notify = monitor.user32.NotifyWinEvent
        notify.argtypes = [wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG]
        notify.restype = None
        hwnd = monitor.user32.GetForegroundWindow()
        if not hwnd:
            pytest.skip("No accessible Windows foreground window; use an interactive unlocked desktop")
        notify(0x800C, hwnd, 0, 0)
        assert monitor.wait(2)
    finally:
        monitor.close()
    assert not monitor._thread.is_alive()


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="requires a live Windows desktop")
def test_native_tray_settings_and_clean_shutdown(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.pop("QT_QPA_PLATFORM", None)
    environment.pop("QT_SCALE_FACTOR", None)
    result = subprocess.run(
        [sys.executable, str(root / "scripts/smoke_desktop.py"), "--db", str(tmp_path / "native.sqlite3"), "--duration", "3"],
        cwd=root, env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    status, database = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert status.pop("background_alpha") == 255
    assert status == {"platform": "windows", "popup_visible": True, "pinned": True,
                      "tray_visible": True, "settings_visible": True, "worker_running": True, "error": None}
    assert database == {"open_sessions": 0, "overlaps": 0, "integrity": "ok"}
