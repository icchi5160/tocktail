from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from tocktail.core.models import ActivityState as State, Application, Batch, Session, Target
from tocktail.core.summary import day_bounds
from tocktail.storage.sqlite import Store


DAY = date(2026, 11, 6)
START, END = day_bounds(DAY, timezone.utc)


def session(key, start, end, state=State.ACTIVE, name="editor.exe", *, is_open=False):
    app = Application(name, "c:/apps/" + name) if name else None
    return Session(key, Target(app, 1, state), start, end, is_open)


def test_today_clips_at_local_midnight_and_excludes_non_usage(tmp_path):
    with Store(tmp_path / "db") as store:
        store.save(Batch((
            session("previous", START - 30000, START + 30000),
            session("passive", START + 30000, START + 40000, State.PASSIVE),
            session("idle", START + 40000, START + 50000, State.IDLE),
            session("paused", START + 50000, START + 60000, State.PAUSED, None),
            session("tomorrow", END - 10000, END + 10000, State.ACTIVE, "other.exe"),
        )))
        summary = store.daily_summary(DAY, zone=timezone.utc)
        assert summary.active_ms == 40000
        assert summary.passive_ms == 10000
        assert summary.idle_ms == 10000
        assert summary.total_ms == 50000
        assert [(app.name, app.duration_ms) for app in summary.top_apps] == [
            ("editor.exe", 40000), ("other.exe", 10000)]


def test_live_summary_replaces_saved_checkpoint(tmp_path):
    with Store(tmp_path / "db") as store:
        saved = session("open", START, START + 5000, is_open=True)
        store.save(Batch((saved,)))
        live = replace(saved, ended_at=START + 6000)
        summary = store.daily_summary(DAY, current=live, zone=timezone.utc)
        assert summary.total_ms == 6000
        assert summary.top_apps[0].duration_ms == 6000
        assert store.daily_summary(DAY, zone=timezone.utc).total_ms == 5000


def test_top_five_aggregates_by_executable(tmp_path):
    with Store(tmp_path / "db") as store:
        rows = []
        offset = START
        for index in range(8):
            duration = (index + 1) * 1000
            rows.append(session(str(index), offset, offset + duration, name=f"app{index}.exe"))
            offset += duration
        rows.append(session("again", offset, offset + 20000, name="app0.exe"))
        store.save(Batch(tuple(rows)))
        summary = store.daily_summary(DAY, zone=timezone.utc)
        assert len(summary.top_apps) == 5
        assert summary.top_apps[0].name == "app0.exe"
        assert summary.top_apps[0].duration_ms == 21000
        assert summary.total_ms == 56000


def test_empty_day(tmp_path):
    with Store(tmp_path / "db") as store:
        summary = store.daily_summary(DAY, zone=timezone.utc)
        assert summary.total_ms == summary.idle_ms == 0
        assert not summary.top_apps


def test_local_date_bounds_use_japan_midnight():
    start, end = day_bounds(DAY, timezone(timedelta(hours=9)))
    assert datetime.fromtimestamp(start / 1000, timezone.utc).hour == 15
    assert end - start == 86400000
