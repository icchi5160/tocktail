from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import logging
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

from tocktail.browser.config import BridgeConfig
from tocktail.browser.matching import BrowserIdentity, WindowEvidence, matches
from tocktail.browser.protocol import MAX_AGE_MS, BrowserSnapshot, decode_json, envelope, snapshot
from tocktail.core.models import Content, Event, Observation
from tocktail.config.settings import Settings

if TYPE_CHECKING:
    from tocktail.browser.pipe import Received


class Receiver(Protocol):
    def drain(self) -> tuple[Received, ...]: ...
    def close(self) -> None: ...


class EvidenceSource(Protocol):
    def browser_ancestor(self, pid: int) -> BrowserIdentity | None: ...
    def window_evidence(self, observation: Observation) -> WindowEvidence: ...


@dataclass(frozen=True)
class Cached:
    connection: str
    identity: BrowserIdentity
    stream: str
    value: BrowserSnapshot
    received_ms: int
    parsed_ms: int
    source_age_ms: int


class BrowserBridge:
    def __init__(self, config: BridgeConfig, server: Receiver, inspector: EvidenceSource) -> None:
        self.config, self.server, self.inspector = config, server, inspector
        self._cache: dict[int, Cached] = {}
        self._rejected: dict[int, str] = {}
        self._candidate: object = None
        self._candidate_since = 0

    def observe(self, observation: Observation, settings: Settings) -> tuple[Observation, tuple[Event, ...]]:
        events: list[Event] = []
        try:
            updates = self.server.drain()
        except OSError:
            self._cache.clear()
            updates = ()
        for item in updates:
            previous = self._cache.get(item.slot)
            if item.data is None:
                self._cache.pop(item.slot, None)
                self._rejected.pop(item.slot, None)
                if previous is not None:
                    events.append(Event("BROWSER_DISCONNECTED", observation.utc_ms, "browser",
                                        {"browser": previous.identity.family}))
                continue
            if self._rejected.get(item.slot) == item.connection:
                continue
            try:
                origin, stream, value = envelope(decode_json(item.data))
                same_connection = previous is not None and previous.connection == item.connection
                identity = previous.identity if same_connection else self.inspector.browser_ancestor(item.peer_pid)
                if identity is None or not self.config.allows(identity.family, origin):
                    raise ValueError("Untrusted browser peer")
                current = snapshot(value, observation.utc_ms)
                if same_connection and (current.sequence <= previous.value.sequence or stream != previous.stream):
                    raise ValueError("Replayed browser message")
                if not same_connection:
                    events.append(Event("BROWSER_CONNECTED", observation.utc_ms, "browser", {"browser": identity.family}))
                self._cache[item.slot] = Cached(item.connection, identity, stream, current, item.monotonic_ms,
                                                 observation.monotonic_ms, max(0, observation.utc_ms - current.timestamp_ms))
                self._rejected.pop(item.slot, None)
            except (ValueError, OSError):
                self._cache.pop(item.slot, None)
                self._rejected[item.slot] = item.connection
        fresh = [item for item in self._cache.values()
                 if 0 <= observation.monotonic_ms - item.received_ms <= MAX_AGE_MS
                 and item.source_age_ms + max(0, observation.monotonic_ms - item.parsed_ms) <= MAX_AGE_MS]
        candidates = []
        if observation.application and observation.application.process_name.casefold() in {"chrome.exe", "msedge.exe"}:
            try:
                evidence = self.inspector.window_evidence(observation)
                candidates = [item for item in fresh if matches(observation, item.value, item.identity, evidence)]
            except OSError:
                pass
        if len(candidates) != 1:
            self._candidate = None
            return self._fallback(observation), tuple(events)
        item = candidates[0]
        value = item.value
        key = (item.connection, value.window_id, value.tab_id, value.navigation, value.domain,
               observation.hwnd, observation.pid)
        if key != self._candidate:
            self._candidate, self._candidate_since = key, observation.monotonic_ms
            return self._fallback(observation), tuple(events)
        if observation.monotonic_ms - self._candidate_since < 250:
            return self._fallback(observation), tuple(events)
        if settings.excludes_domain(value.domain):
            return replace(self._fallback(observation), excluded=True, title=""), tuple(events)
        recorded_title = value.title if settings.save_titles else ""
        content_key = hashlib.sha256(json_key(item.stream, value, recorded_title).encode()).hexdigest()
        content = Content(content_key, recorded_title, value.domain)
        # Media evidence must expire even if the transport heartbeat stops.
        media_fresh = (item.source_age_ms + max(0, observation.monotonic_ms - item.parsed_ms)
                       + value.media_age_ms <= 2500)
        return replace(observation, content=content, browser_connected=True, content_verified=True,
                       media_playing=value.media_playing and media_fresh,
                       browser_tab_id=value.tab_id, browser_window_id=value.window_id,
                       browser_navigation=value.navigation), tuple(events)

    @staticmethod
    def _fallback(observation: Observation) -> Observation:
        browser = observation.application and observation.application.process_name.casefold() in {"chrome.exe", "msedge.exe"}
        return replace(observation, content=None, browser_connected=False, content_verified=False,
                       media_playing=False, browser_tab_id=None, browser_window_id=None, browser_navigation=None,
                       title="" if browser else observation.title)

    def close(self) -> None:
        self._cache.clear()
        self.server.close()


def json_key(stream: str, value: BrowserSnapshot, title: str) -> str:
    return json.dumps([stream, value.tab_id, value.navigation, value.domain, title], ensure_ascii=False)


def open_bridge(database: Path) -> BrowserBridge | None:
    try:
        config = BridgeConfig.load()
        if config.database.resolve() != database.resolve():
            return None
        from tocktail.browser.pipe import PipeServer
        from tocktail.browser.windows import WindowsEvidence
        inspector = WindowsEvidence()
        server = PipeServer(config.pipe_name(inspector.user_sid()))
        server.start()
        return BrowserBridge(config, server, inspector)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RuntimeError):
        logging.getLogger(__name__).warning("Browser integration unavailable; continuing application tracking")
        return None
