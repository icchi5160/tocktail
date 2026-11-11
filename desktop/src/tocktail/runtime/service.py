from dataclasses import replace
from datetime import date
import os

from tocktail.config.settings import Settings
from tocktail.core.engine import SessionEngine
from tocktail.core.models import Batch, Event, Observation
from tocktail.core.summary import DashboardSnapshot
from tocktail.storage.sqlite import Store


class TrackingService:
    """One worker owns this service, its engine and its SQLite connection."""

    def __init__(self, store: Store, *, own_pid: int | None = None) -> None:
        self.store = store
        store.recover()
        self.settings = Settings.from_dict(store.get_setting("desktop", {}),
                                           idle_seconds=store.get_setting("idle_seconds", 300))
        self.engine = SessionEngine(idle_threshold_ms=int(self.settings.idle_seconds * 1000),
                                    initial_floor_ms=store.last_end_ms)
        self.paused = False
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._last_title = ""
        self._app_identity: object = None
        self._app_since = 0
        self._healthy = True
        self._browser_detail: tuple[object, ...] | None = None
        self._last_browser_identity: tuple[object, ...] | None = None
        self._media_playing = False

    def configure(self, settings: Settings) -> None:
        self.store.set_settings({"desktop": settings.to_dict(), "idle_seconds": settings.idle_seconds})
        self.settings = settings
        self.engine.idle_threshold_ms = int(settings.idle_seconds * 1000)

    def observe(self, observation: Observation, extra_events: tuple[Event, ...] = ()) -> None:
        verified = observation.content_verified and observation.browser_connected and observation.content is not None
        excluded = observation.excluded or observation.pid == self._own_pid or (
            observation.application is not None
            and observation.application.process_name.casefold() in self.settings.excluded_apps
        ) or (verified and self.settings.excludes_domain(observation.content.domain))
        browser_app = observation.application and observation.application.process_name.casefold() in {"chrome.exe", "msedge.exe"}
        allow_title = self.settings.save_titles and not excluded and (not browser_app or verified)
        content = observation.content
        if content is not None and not self.settings.save_titles:
            content = replace(content, title="")
        observation = replace(observation, excluded=excluded, paused=self.paused,
                              title=observation.title if allow_title else "", content=content)
        batch = self.engine.observe(observation)
        current = self.engine.current
        assert current is not None
        events = list(extra_events)
        detail = ((current.target.application.executable_path, observation.browser_window_id,
                   observation.browser_tab_id, observation.browser_navigation or current.target.content.key)
                  if current.target.content is not None else None)
        if detail is not None and detail != self._last_browser_identity:
            event_type = ("BROWSER_TAB_CHANGED" if self._last_browser_identity is None or detail[:3] != self._last_browser_identity[:3]
                          else "BROWSER_NAVIGATED")
            events.append(Event(event_type, current.ended_at, "browser", {}))
        elif detail is None and self._browser_detail is not None:
            events.append(Event("BROWSER_DETAILS_UNAVAILABLE", current.ended_at, "browser", {}))
        playing = bool(detail and observation.media_playing)
        if playing != self._media_playing:
            events.append(Event("MEDIA_STATE_CHANGED", current.ended_at, "browser", {"playing": playing}))
        self._browser_detail, self._media_playing = detail, playing
        if detail is not None:
            self._last_browser_identity = detail
        if events:
            batch = Batch(batch.sessions, batch.events + tuple(events))
        if self.settings.save_titles and not excluded and current.target.application:
            if observation.title != self._last_title:
                batch = Batch(batch.sessions, batch.events + (Event(
                    "WINDOW_TITLE_OBSERVED", current.ended_at, observation.source,
                    {"title": observation.title, "hwnd": observation.hwnd},
                ),))
        self._last_title = observation.title
        identity = (current.target.application, current.target.hwnd) if current.target.application else None
        gap = any(event.event_type == "OBSERVATION_GAP" for event in batch.events)
        if identity != self._app_identity or gap:
            self._app_since = current.started_at
        self._app_identity = identity
        try:
            self.store.save(batch)
        except Exception:
            self._healthy = False
            raise

    def snapshot(self, day: date | None = None) -> DashboardSnapshot:
        current = self.engine.current
        summary = self.store.daily_summary(day or date.today(), current)
        if current is None:
            from tocktail.core.models import ActivityState
            return DashboardSnapshot(summary, ActivityState.UNTRACKED, paused=self.paused)
        app = current.target.application
        return DashboardSnapshot(summary, current.target.state, app.process_name if app else None,
                                 max(0, current.ended_at - self._app_since) if app else 0,
                                 current.target.excluded, self.paused,
                                 current.target.content.domain if current.target.content else None)

    def close(self) -> None:
        if self._healthy:
            self.store.save(self.engine.stop())
