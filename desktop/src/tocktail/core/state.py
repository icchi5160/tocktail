from tocktail.core.models import ActivityState, Observation, Target


def classify(observation: Observation, idle_threshold_ms: int = 300_000) -> Target:
    """Only confirmed foreground media can justify PASSIVE time."""
    if idle_threshold_ms <= 0:
        raise ValueError("idle threshold must be positive")
    content = (
        observation.content
        if observation.browser_connected and observation.content_verified
        else None
    )
    if observation.suspended:
        state = ActivityState.SUSPENDED
    elif observation.locked:
        state = ActivityState.LOCKED
    elif observation.paused:
        state = ActivityState.PAUSED
    elif observation.excluded or observation.application is None:
        state = ActivityState.UNTRACKED
    elif max(0, observation.idle_ms) < idle_threshold_ms:
        state = ActivityState.ACTIVE
    elif content is not None and observation.media_playing:
        state = ActivityState.PASSIVE
    else:
        state = ActivityState.IDLE
    if state in {
        ActivityState.PAUSED, ActivityState.LOCKED,
        ActivityState.SUSPENDED, ActivityState.UNTRACKED,
    }:
        return Target(None, None, state, excluded=observation.excluded)
    return Target(observation.application, observation.hwnd, state, content)
