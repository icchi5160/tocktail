"""Single-stream accounting using a UTC anchor and monotonic elapsed time."""

from dataclasses import replace
from uuid import uuid4

from tocktail.core.models import (
    ActivityState, Batch, Event, Observation, Session, Target,
)
from tocktail.core.state import classify


class SessionEngine:
    def __init__(
        self, idle_threshold_ms: int = 300_000, checkpoint_ms: int = 5_000,
        max_gap_ms: int = 5_000, initial_floor_ms: int = 0,
    ) -> None:
        if min(idle_threshold_ms, checkpoint_ms, max_gap_ms) <= 0:
            raise ValueError("timing intervals must be positive")
        self.idle_threshold_ms = idle_threshold_ms
        self.checkpoint_ms = checkpoint_ms
        self.max_gap_ms = max_gap_ms
        self._floor = initial_floor_ms
        self._last: Observation | None = None
        self._logical_ms = 0
        self._checkpoint_at = 0
        self.current: Session | None = None

    def observe(self, observation: Observation) -> Batch:
        previous = self._last
        if previous and observation.monotonic_ms < previous.monotonic_ms:
            return Batch(events=(Event("STALE_OBSERVATION", self._logical_ms,
                                       "core", {}),))
        target = classify(observation, self.idle_threshold_ms)
        events: list[Event] = []
        updates: list[Session] = []
        delta = 0 if previous is None else observation.monotonic_ms - previous.monotonic_ms
        now = max(self._floor, observation.utc_ms) if previous is None else self._logical_ms + delta
        # A clock step changes the anchor only across an explicitly untracked gap.
        inactive = previous is not None and (previous.locked or previous.suspended)
        gap = previous is not None and (delta > self.max_gap_ms or observation.dropped or (
            inactive and observation.utc_ms - previous.utc_ms > self.max_gap_ms))
        if gap:
            if self.current:
                updates.append(replace(self.current, ended_at=self._logical_ms,
                                       is_open=False, end_reason="OBSERVATION_GAP"))
            now = max(now, observation.utc_ms)
            if now > self._logical_ms:
                gap_state = (ActivityState.SUSPENDED if previous.suspended else ActivityState.LOCKED) if inactive else ActivityState.UNTRACKED
                updates.append(Session(str(uuid4()), Target(None, None, gap_state),
                                       self._logical_ms, now, False, "OBSERVATION_GAP"))
            self.current = None
            events.append(Event("OBSERVATION_GAP", now, "core", {"elapsed_ms": delta}))
        if previous and abs(observation.utc_ms - previous.utc_ms - delta) > 2_000:
            events.append(Event("CLOCK_ADJUSTED", now, "core",
                                {"observed_utc_ms": observation.utc_ms}))
        if previous:
            for field, entered, left in (("locked", "SESSION_LOCKED", "SESSION_UNLOCKED"),
                                          ("suspended", "SYSTEM_SUSPENDED", "SYSTEM_RESUMED")):
                if getattr(previous, field) != getattr(observation, field):
                    events.append(Event(entered if getattr(observation, field) else left,
                                        now, observation.source, {}))
        old_target = self.current.target if self.current else None
        if old_target != target:
            reason = self._reason(old_target, target)
            if self.current:
                updates.append(replace(self.current, ended_at=now, is_open=False, end_reason=reason))
            self.current = Session(str(uuid4()), target, now, now, True)
            updates.append(self.current)
            self._checkpoint_at = now
            events.append(Event(reason, now, observation.source, {
                "state": target.state.value,
                "process_name": target.application.process_name if target.application else None,
                "executable_path": target.application.executable_path if target.application else None,
                "hwnd": target.hwnd,
                "pid": observation.pid if target.application else None,
                "idle_ms": max(0, observation.idle_ms) if target.application else None,
                "content_key": target.content.key if target.content else None,
                "excluded": target.excluded,
                "observed_utc_ms": observation.utc_ms,
            }))
        else:
            assert self.current is not None
            self.current = replace(self.current, ended_at=now)
            if now - self._checkpoint_at >= self.checkpoint_ms:
                updates.append(self.current)
                self._checkpoint_at = now
        if previous and observation.title != previous.title and target.application:
            # Titles are deliberately absent from the core event payload.
            events.append(Event("WINDOW_TITLE_CHANGED", now, observation.source, {}))
        self._last = observation
        self._logical_ms = now
        return Batch(tuple(updates), tuple(events))

    @staticmethod
    def _reason(old: Target | None, new: Target) -> str:
        if old is None:
            return "SESSION_STARTED"
        if new.state == ActivityState.PAUSED and old.state != ActivityState.PAUSED:
            return "TRACKING_PAUSED"
        if old.state == ActivityState.PAUSED and new.state != ActivityState.PAUSED:
            return "TRACKING_RESUMED"
        if old.excluded != new.excluded:
            return "TRACKING_EXCLUDED" if new.excluded else "TRACKING_EXCLUSION_ENDED"
        if old.application != new.application or old.hwnd != new.hwnd:
            return "FOREGROUND_CHANGED"
        if old.content != new.content:
            return "CONTENT_CHANGED"
        if new.state == ActivityState.IDLE:
            return "IDLE_THRESHOLD_REACHED"
        if new.state == ActivityState.ACTIVE:
            return "ACTIVITY_RESUMED"
        return "ACTIVITY_STATE_CHANGED"

    def stop(self, reason: str = "SHUTDOWN") -> Batch:
        """Close at the last observation, never extrapolate unobserved time."""
        if self.current is None:
            return Batch()
        session = replace(self.current, is_open=False, end_reason=reason)
        self._floor = session.ended_at
        self.current = None
        self._last = None
        return Batch((session,), (Event(reason, session.ended_at, "core", {}),))
