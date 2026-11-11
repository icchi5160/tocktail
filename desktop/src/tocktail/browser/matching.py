"""OS-independent correlation policy; extension IDs are never treated as HWNDs."""

from dataclasses import dataclass
from collections.abc import Callable, Mapping

from tocktail.browser.protocol import BrowserSnapshot
from tocktail.core.models import Observation


@dataclass(frozen=True)
class BrowserIdentity:
    family: str
    pid: int


@dataclass(frozen=True)
class NativeWindow:
    hwnd: int
    pid: int
    title: str | None = None


@dataclass(frozen=True)
class WindowEvidence:
    foreground_hwnd: int | None
    title: str
    windows: tuple[NativeWindow, ...]
    complete: bool = True


def browser_root(pid: int, parents: Mapping[int, int], process_name: Callable[[int], str | None]) -> BrowserIdentity | None:
    identity = None
    seen: set[int] = set()
    for _ in range(16):
        if pid in seen:
            return None
        seen.add(pid)
        name = process_name(pid)
        if name in {"chrome.exe", "msedge.exe"}:
            family = "chrome" if name == "chrome.exe" else "edge"
            if identity is not None and identity.family != family:
                return identity
            identity = BrowserIdentity(family, pid)
        elif identity is not None:
            return identity
        following = parents.get(pid)
        if not following or following == pid:
            return identity
        pid = following
    return None


def matches(observation: Observation, browser: BrowserSnapshot, identity: BrowserIdentity,
            evidence: WindowEvidence) -> bool:
    expected = {"chrome": ("chrome.exe", "Google Chrome"), "edge": ("msedge.exe", "Microsoft Edge")}
    if identity.family not in expected:
        return False
    process, suffix = expected[identity.family]
    if not observation.application or observation.application.process_name.casefold() != process:
        return False
    if observation.pid != identity.pid or not observation.hwnd or not evidence.complete:
        return False
    if evidence.foreground_hwnd != observation.hwnd or not browser.focused or browser.window_count < 1:
        return False
    foreground = [window for window in evidence.windows
                  if window.hwnd == observation.hwnd and window.pid == identity.pid]
    if len(foreground) != 1:
        return False
    if browser.domain is None or not browser.title or browser.tab_id is None:
        return False
    expected_title = f"{browser.title} - {suffix}"
    if evidence.title != expected_title:
        return False
    if len(evidence.windows) == 1:
        return browser.window_count == 1 and foreground[0].title in (None, expected_title)
    # Focus + authenticated root PID + a unique native title identify the foreground
    # among multiple windows. Duplicate/unknown titles cannot establish that binding.
    # Count only this root for the extension's inventory; other profiles may share
    # the executable family but cannot supply this connection's windows.
    if browser.window_count > sum(window.pid == identity.pid for window in evidence.windows):
        return False
    if any(not window.title for window in evidence.windows):
        return False
    matching = [window for window in evidence.windows if window.title == expected_title]
    return len(matching) == 1 and matching[0] == foreground[0]
