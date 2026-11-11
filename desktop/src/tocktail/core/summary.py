"""UI-facing immutable values and civil-day boundaries; no Qt or OS APIs."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from tocktail.core.models import ActivityState


@dataclass(frozen=True)
class AppUsage:
    name: str
    executable_path: str
    duration_ms: int


@dataclass(frozen=True)
class DailySummary:
    day: date
    active_ms: int = 0
    passive_ms: int = 0
    idle_ms: int = 0
    top_apps: tuple[AppUsage, ...] = ()

    @property
    def total_ms(self) -> int:
        return self.active_ms + self.passive_ms


@dataclass(frozen=True)
class DashboardSnapshot:
    summary: DailySummary
    state: ActivityState
    current_app: str | None = None
    current_duration_ms: int = 0
    excluded: bool = False
    paused: bool = False
    current_domain: str | None = None


def day_bounds(day: date, zone: tzinfo | None = None) -> tuple[int, int]:
    """Naive local timestamps use each midnight's OS timezone/DST rules."""
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
