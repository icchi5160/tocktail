from datetime import datetime
import json
import sqlite3

import pytest

from tocktail.config.settings import Settings
from tocktail.core.models import ActivityState as State, Content
from tocktail.runtime.service import TrackingService
from tocktail.storage.sqlite import Store


def sample_day(observation):
    return datetime.fromtimestamp(observation().utc_ms / 1000).date()


def test_pause_resume_splits_and_excludes_paused_time(tmp_path, observation):
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        service.observe(observation())
        service.paused = True
        service.observe(observation(1))
        service.observe(observation(2))
        assert service.snapshot(sample_day(observation)).paused
        service.paused = False
        service.observe(observation(3))
        service.observe(observation(4))
        assert service.snapshot(sample_day(observation)).summary.total_ms == 2000
        service.close()
        events = {row[0] for row in store.connection.execute("SELECT event_type FROM activity_events")}
        assert {"TRACKING_PAUSED", "TRACKING_RESUMED"} <= events
        assert store.connection.execute("SELECT count(*) FROM sessions WHERE is_open=1").fetchone()[0] == 0


def test_settings_persist_and_apply_without_restart(tmp_path, observation):
    path = tmp_path / "db"
    with Store(path) as store:
        service = TrackingService(store, own_pid=999)
        service.observe(observation())
        service.configure(Settings(idle_seconds=1, pinned=True, always_on_top=False))
        service.observe(observation(1, idle_ms=1000))
        assert service.snapshot(sample_day(observation)).state == State.IDLE
        assert service.snapshot(sample_day(observation)).current_duration_ms == 1000
        service.close()
    with Store(path) as store:
        restarted = TrackingService(store, own_pid=999)
        assert restarted.settings.idle_seconds == 1
        assert restarted.settings.pinned
        assert not restarted.settings.always_on_top


@pytest.mark.parametrize("own", [True, False])
def test_own_app_and_user_exclusions_have_no_identity(tmp_path, observation, own):
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=20 if own else 999)
        if not own:
            service.configure(Settings(excluded_apps=("editor.exe",), save_titles=True))
        service.observe(observation(title="Private input"))
        service.observe(observation(1, title="Private input"))
        snapshot = service.snapshot(sample_day(observation))
        assert snapshot.excluded and snapshot.current_app is None
        assert snapshot.summary.total_ms == 0
        assert store.connection.execute("SELECT count(*) FROM applications").fetchone()[0] == 0
        payloads = [row[0] for row in store.connection.execute("SELECT payload_json FROM activity_events")]
        assert "Private" not in repr(payloads) and "editor" not in repr(payloads)


def test_title_preference_is_prospective(tmp_path, observation):
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        service.observe(observation(title="Never saved"))
        service.configure(Settings(save_titles=True))
        service.observe(observation(1, title="Allowed title"))
        service.configure(Settings(save_titles=False))
        service.observe(observation(2, title="Never saved either"))
        payloads = [json.loads(row[0]) for row in store.connection.execute("SELECT payload_json FROM activity_events")]
        assert any(payload.get("title") == "Allowed title" for payload in payloads)
        assert "Never saved" not in repr(payloads)


def test_failed_save_does_not_flush_advanced_engine(tmp_path, observation, monkeypatch):
    path = tmp_path / "db"
    with Store(path) as store:
        service = TrackingService(store, own_pid=999)
        service.observe(observation())

        def fail(batch):
            raise sqlite3.OperationalError("disk full")

        with monkeypatch.context() as patch:
            patch.setattr(store, "save", fail)
            with pytest.raises(sqlite3.OperationalError):
                service.observe(observation(1, hwnd=99))
        service.close()
        assert store.recover() == 1
        assert store.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


@pytest.mark.parametrize("transition", ["disconnect", "unverified", "excluded", "paused", "locked", "different_site"])
def test_snapshot_exposes_only_current_verified_site(tmp_path, observation, transition):
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        content = Content("site", "Private page title", "example.com")
        service.observe(observation(content=content, browser_connected=True, content_verified=True))
        assert service.snapshot(sample_day(observation)).current_domain == "example.com"
        assert service.engine.current.target.content.title == ""  # Title saving remains off.
        fields = dict(content=content, browser_connected=True, content_verified=True)
        if transition == "disconnect":
            fields["browser_connected"] = False
        elif transition == "unverified":
            fields["content_verified"] = False
        elif transition == "excluded":
            service.configure(Settings(excluded_domains=("example.com",)))
        elif transition == "paused":
            service.paused = True
        elif transition == "locked":
            fields["locked"] = True
        else:
            fields["content"] = Content("next", "", "other.example")
        service.observe(observation(1, **fields))
        assert service.snapshot(sample_day(observation)).current_domain == (
            "other.example" if transition == "different_site" else None)
