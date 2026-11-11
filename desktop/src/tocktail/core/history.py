"""Immutable dashboard results; all intervals are half-open UTC milliseconds."""

from dataclasses import dataclass
from datetime import date

from tocktail.core.summary import AppUsage


@dataclass(frozen=True)
class SiteUsage:
    domain: str
    duration_ms: int


@dataclass(frozen=True)
class HistorySession:
    started_at: int
    ended_at: int
    state: str
    app: str
    domain: str
    excluded: bool


@dataclass(frozen=True)
class HistoryReport:
    start: date
    end: date
    states: tuple[tuple[str, int], ...]
    applications: tuple[AppUsage, ...]
    sites: tuple[SiteUsage, ...]
    days: tuple[tuple[date, int], ...]
    sessions: tuple[HistorySession, ...]

    @property
    def total_ms(self) -> int:
        return sum(value for state, value in self.states if state in {"ACTIVE", "PASSIVE"})
