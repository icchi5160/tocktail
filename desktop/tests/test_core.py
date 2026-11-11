from dataclasses import replace
from datetime import datetime, timedelta, timezone
import random

import pytest

from tocktail.core.aggregation import total_usage_ms, usage_ms
from tocktail.core.engine import SessionEngine
from tocktail.core.models import ActivityState as State, Application, Content, Target
from tocktail.core.state import classify


def test_session_start_checkpoint_end(observation):
    engine = SessionEngine()
    first = engine.observe(observation())
    assert first.sessions[0].is_open
    assert first.sessions[0].target.state == State.ACTIVE
    for second in range(1, 5):
        assert not engine.observe(observation(second)).sessions
    checkpoint = engine.observe(observation(5))
    assert checkpoint.sessions[0].ended_at - checkpoint.sessions[0].started_at == 5000
    stopped = engine.stop()
    assert stopped.sessions[0].end_reason == "SHUTDOWN"
    assert not stopped.sessions[0].is_open
    assert not engine.stop().sessions


@pytest.mark.parametrize("change", [
    {"application": Application("other.exe", "c:\\other.exe")},
    {"hwnd": 11},
    {"content": Content("tab1"), "browser_connected": True, "content_verified": True},
])
def test_switch_splits_with_no_overlap(observation, change):
    engine = SessionEngine()
    engine.observe(observation())
    batch = engine.observe(observation(2, **change))
    assert len(batch.sessions) == 2
    old, new = batch.sessions
    assert old.ended_at == new.started_at
    assert not old.is_open and new.is_open
    assert old.id != new.id


def test_active_to_idle_and_resume(observation):
    engine = SessionEngine(idle_threshold_ms=3000)
    engine.observe(observation())
    engine.observe(observation(1, idle_ms=1000))
    batch = engine.observe(observation(3, idle_ms=3000))
    assert engine.current.target.state == State.IDLE
    assert batch.events[0].event_type == "IDLE_THRESHOLD_REACHED"
    batch = engine.observe(observation(4, idle_ms=0))
    assert engine.current.target.state == State.ACTIVE
    assert batch.events[0].event_type == "ACTIVITY_RESUMED"


def test_active_passive_idle(observation):
    engine = SessionEngine(idle_threshold_ms=1000)
    evidence = dict(content=Content("media"), browser_connected=True,
                    content_verified=True, media_playing=True)
    engine.observe(observation(0, **evidence))
    assert engine.current.target.state == State.ACTIVE
    engine.observe(observation(1, idle_ms=1000, **evidence))
    assert engine.current.target.state == State.PASSIVE
    engine.observe(observation(2, idle_ms=2000, **{**evidence, "media_playing": False}))
    assert engine.current.target.state == State.IDLE


@pytest.mark.parametrize("evidence", [
    {"browser_connected": False}, {"content_verified": False},
    {"content": None}, {"media_playing": False},
])
def test_unconfirmed_media_cannot_be_passive(observation, evidence):
    params = dict(content=Content("tab"), browser_connected=True,
                  content_verified=True, media_playing=True, idle_ms=300000)
    assert classify(observation(**{**params, **evidence})).state == State.IDLE


@pytest.mark.parametrize("idle_ms,expected", [(0, State.ACTIVE), (300000, State.IDLE)])
def test_browser_disconnect_falls_back_to_app(observation, idle_ms, expected):
    engine = SessionEngine()
    evidence = dict(content=Content("site"), content_verified=True)
    engine.observe(observation(browser_connected=True, media_playing=True, idle_ms=idle_ms, **evidence))
    assert engine.current.target.content is not None
    batch = engine.observe(observation(1, browser_connected=False, media_playing=True, idle_ms=idle_ms, **evidence))
    assert len(batch.sessions) == 2
    assert engine.current.target.content is None
    assert engine.current.target.application == observation().application
    assert engine.current.target.state == expected


def test_content_to_content_transition(observation):
    engine = SessionEngine()
    engine.observe(observation(content=Content("tab1"), browser_connected=True, content_verified=True))
    batch = engine.observe(observation(1, content=Content("tab2"), browser_connected=True, content_verified=True))
    assert len(batch.sessions) == 2
    assert batch.events[0].event_type == "CONTENT_CHANGED"
    assert batch.sessions[0].ended_at == batch.sessions[1].started_at


def test_duplicate_observation_does_not_split_session(observation):
    engine = SessionEngine()
    engine.observe(observation())
    assert not engine.observe(observation()).sessions


@pytest.mark.parametrize("flag,state", [
    ("paused", State.PAUSED), ("locked", State.LOCKED),
    ("suspended", State.SUSPENDED), ("excluded", State.UNTRACKED),
])
def test_non_usage_state_has_no_app_or_content(observation, flag, state):
    target = classify(observation(**{flag: True}, content=Content("private"),
                                  content_verified=True, browser_connected=True))
    assert target == Target(None, None, state, excluded=flag == "excluded")


def test_excluded_and_unavailable_periods_are_distinct(observation):
    engine = SessionEngine()
    engine.observe(observation(application=None))
    batch = engine.observe(observation(1, excluded=True))
    assert len(batch.sessions) == 2
    assert batch.sessions[0].target.excluded is False
    assert batch.sessions[1].target.excluded is True
    assert batch.events[0].event_type == "TRACKING_EXCLUDED"
    assert "editor" not in repr(batch.events)


def test_unavailable_foreground_is_untracked(observation):
    assert classify(observation(application=None)).state == State.UNTRACKED


def test_sleep_gap_is_not_usage(observation):
    engine = SessionEngine()
    engine.observe(observation())
    engine.observe(observation(1))
    batch = engine.observe(observation(60))
    old, gap, resumed = batch.sessions
    assert old.ended_at == gap.started_at
    assert gap.ended_at == resumed.started_at
    assert gap.target.state == State.UNTRACKED
    assert gap.ended_at - gap.started_at == 59000
    assert total_usage_ms(usage_ms(batch.sessions, old.started_at, resumed.ended_at)) == 1000


def test_explicit_suspend_and_resume(observation):
    engine = SessionEngine(max_gap_ms=120000)
    all_sessions = []
    for sample in [observation(), observation(1, suspended=True), observation(61)]:
        all_sessions.extend(s for s in engine.observe(sample).sessions if not s.is_open)
    engine.observe(observation(62))
    all_sessions.extend(engine.stop().sessions)
    totals = usage_ms(all_sessions, observation().utc_ms, observation(62).utc_ms)
    assert totals[State.SUSPENDED] == 60000
    assert total_usage_ms(totals) == 2000


def test_dropped_observations_make_gap_even_below_timeout(observation):
    engine = SessionEngine()
    engine.observe(observation())
    batch = engine.observe(observation(1, dropped=True))
    assert any(s.target.state == State.UNTRACKED for s in batch.sessions)


@pytest.mark.parametrize("wall_step", [-3600000, 3600000])
def test_clock_step_uses_monotonic_elapsed(observation, wall_step):
    engine = SessionEngine()
    engine.observe(observation())
    sample = observation(1)
    batch = engine.observe(replace(sample, utc_ms=sample.utc_ms + wall_step))
    assert any(e.event_type == "CLOCK_ADJUSTED" for e in batch.events)
    assert engine.current.ended_at - engine.current.started_at == 1000


def test_stale_event_does_not_move_time_backwards(observation):
    engine = SessionEngine()
    engine.observe(observation(2))
    batch = engine.observe(observation(1))
    assert not batch.sessions
    assert batch.events[0].event_type == "STALE_OBSERVATION"
    assert engine.current.ended_at == observation(2).utc_ms


def test_restart_floor_with_backwards_wall_clock(observation):
    engine = SessionEngine(initial_floor_ms=observation(60).utc_ms)
    engine.observe(observation())
    assert engine.current.started_at == observation(60).utc_ms


def test_stop_and_resume_same_engine(observation):
    engine = SessionEngine()
    engine.observe(observation())
    engine.observe(observation(2))
    end = engine.stop().sessions[0].ended_at
    engine.observe(observation(1))
    assert engine.current.started_at >= end


def test_midnight_clipping(observation):
    engine = SessionEngine(max_gap_ms=120000)
    engine.observe(observation())
    engine.observe(observation(120))
    session = engine.stop().sessions[0]
    # A supplied UTC boundary can represent midnight in any timezone.
    midnight = session.started_at + 60000
    before = usage_ms([session], session.started_at, midnight)
    after = usage_ms([session], midnight, session.ended_at)
    assert total_usage_ms(before) == total_usage_ms(after) == 60000
    assert total_usage_ms(usage_ms([session], session.ended_at, session.ended_at + 1)) == 0


def test_japan_midnight_uses_utc_bounds(observation):
    midnight = int(datetime(2026, 11, 7, tzinfo=timezone(timedelta(hours=9))).timestamp() * 1000)
    engine = SessionEngine(max_gap_ms=120000)
    engine.observe(observation(utc_ms=midnight - 30000))
    engine.observe(observation(60, utc_ms=midnight + 30000))
    session = engine.stop().sessions[0]
    assert total_usage_ms(usage_ms([session], midnight - 86400000, midnight)) == 30000
    assert total_usage_ms(usage_ms([session], midnight, midnight + 86400000)) == 30000


def test_titles_are_not_in_core_event_payload(observation):
    engine = SessionEngine()
    engine.observe(observation(title="secret"))
    batch = engine.observe(observation(1, title="another secret"))
    assert "secret" not in repr(batch.events)
    assert batch.events[0].event_type == "WINDOW_TITLE_CHANGED"


def test_randomized_stream_never_overlaps_or_goes_negative(observation):
    rng = random.Random(41)
    engine = SessionEngine(idle_threshold_ms=2000)
    closed = []
    second = 0
    for _ in range(500):
        second += rng.choice([0, 1, 2, 10])
        batch = engine.observe(observation(second, hwnd=rng.choice([1, 2, 3]),
                                           idle_ms=rng.choice([0, 2000, 3000])))
        closed.extend(s for s in batch.sessions if not s.is_open)
    closed.extend(engine.stop().sessions)
    assert all(s.ended_at >= s.started_at for s in closed)
    assert all(a.ended_at <= b.started_at for a, b in zip(closed, closed[1:]))


def test_invalid_timing_parameters():
    with pytest.raises(ValueError):
        SessionEngine(checkpoint_ms=0)
    with pytest.raises(ValueError):
        usage_ms([], 2, 1)
