"""Immutable values exchanged by the monitor, core and storage layers."""

from dataclasses import dataclass
from enum import StrEnum


class ActivityState(StrEnum):
    ACTIVE = "ACTIVE"
    PASSIVE = "PASSIVE"
    IDLE = "IDLE"
    PAUSED = "PAUSED"
    LOCKED = "LOCKED"
    SUSPENDED = "SUSPENDED"
    UNTRACKED = "UNTRACKED"


@dataclass(frozen=True)
class Application:
    process_name: str
    executable_path: str


@dataclass(frozen=True)
class Content:
    """Trusted, already-correlated content from the browser integration layer."""

    key: str
    title: str = ""
    domain: str = ""


@dataclass(frozen=True)
class Observation:
    utc_ms: int
    monotonic_ms: int
    application: Application | None = None
    hwnd: int | None = None
    pid: int | None = None
    title: str = ""
    idle_ms: int = 0
    content: Content | None = None
    browser_connected: bool = False
    content_verified: bool = False
    media_playing: bool = False
    paused: bool = False
    locked: bool = False
    suspended: bool = False
    excluded: bool = False
    source: str = "poll"
    dropped: bool = False
    browser_tab_id: int | None = None
    browser_window_id: int | None = None
    browser_navigation: str | None = None


@dataclass(frozen=True)
class Target:
    application: Application | None
    hwnd: int | None
    state: ActivityState
    content: Content | None = None
    excluded: bool = False


@dataclass(frozen=True)
class Session:
    id: str
    target: Target
    started_at: int
    ended_at: int
    is_open: bool
    end_reason: str | None = None


@dataclass(frozen=True)
class Event:
    event_type: str
    occurred_at: int
    source: str
    payload: dict[str, object]
    schema_version: int = 1


@dataclass(frozen=True)
class Batch:
    sessions: tuple[Session, ...] = ()
    events: tuple[Event, ...] = ()
