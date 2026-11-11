from collections.abc import Iterable

from tocktail.core.models import ActivityState, Session


def usage_ms(sessions: Iterable[Session], start_ms: int, end_ms: int) -> dict[ActivityState, int]:
    """Clip half-open intervals; callers supply UTC bounds for their local day."""
    if end_ms < start_ms:
        raise ValueError("end precedes start")
    totals = {state: 0 for state in ActivityState}
    for session in sessions:
        duration = max(0, min(session.ended_at, end_ms) - max(session.started_at, start_ms))
        totals[session.target.state] += duration
    return totals


def total_usage_ms(totals: dict[ActivityState, int]) -> int:
    return totals[ActivityState.ACTIVE] + totals[ActivityState.PASSIVE]
