"""Small, versioned JSON protocol. Raw page URLs and input content are forbidden."""

from dataclasses import dataclass
import json
import re
from urllib.parse import urlsplit


HOST_NAME = "com.tocktail.bridge"
MAX_MESSAGE = 16_384
MAX_AGE_MS = 4_000
EXTENSION_ID = re.compile(r"[a-p]{32}\Z")
TOKEN = re.compile(r"[a-f0-9]{32}\Z")


def domain_name(value: str) -> str:
    value = value.strip().rstrip(".").lower()
    if not value or len(value) > 253 or any(c in value for c in "/\\:@?#\x00\r\n "):
        raise ValueError("Invalid domain")
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Invalid domain") from error
    if len(value) > 253 or not re.fullmatch(r"[a-z0-9.-]+", value) or any(
        not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in value.split(".")
    ):
        raise ValueError("Invalid domain")
    return value


def origin_url(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid origin URL")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("Only public-format HTTP(S) origins are allowed")
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ValueError("Page paths, queries and fragments must not enter the bridge")
    domain = domain_name(parts.hostname)
    port = parts.port  # Also rejects invalid/out-of-range ports.
    authority = domain + (f":{port}" if port is not None else "")
    return f"{parts.scheme}://{authority}", domain


def integer(value: object, name: str, lower: int = 0, upper: int = 2**53 - 1) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"Invalid {name}")
    return value


@dataclass(frozen=True)
class BrowserSnapshot:
    sequence: int
    timestamp_ms: int
    focused: bool
    window_count: int
    window_id: int | None
    tab_id: int | None
    navigation: str | None
    origin: str | None
    domain: str | None
    title: str
    media_playing: bool
    media_age_ms: int = 0


def decode_json(data: bytes) -> dict[str, object]:
    if not data or len(data) > MAX_MESSAGE:
        raise ValueError("Invalid browser message size")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Invalid browser JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Expected browser object")
    return value


def snapshot(value: object, now_ms: int) -> BrowserSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "version", "sequence", "timestamp_ms", "focused", "window_count", "page",
    } or value["version"] != 1 or type(value["version"]) is not int:
        raise ValueError("Unsupported browser snapshot")
    sequence = integer(value["sequence"], "sequence")
    timestamp = integer(value["timestamp_ms"], "timestamp")
    if not -1000 <= now_ms - timestamp <= MAX_AGE_MS:
        raise ValueError("Stale browser snapshot")
    if type(value["focused"]) is not bool:
        raise ValueError("Invalid focus state")
    count = integer(value["window_count"], "window count", 0, 128)
    focused = value["focused"]
    page = value["page"]
    if page is None:
        return BrowserSnapshot(sequence, timestamp, focused, count, None, None, None, None, None, "", False)
    if not isinstance(page, dict) or set(page) != {
        "window_id", "tab_id", "navigation", "origin", "title", "media",
    }:
        raise ValueError("Invalid page observation")
    window_id = integer(page["window_id"], "window ID")
    tab_id = integer(page["tab_id"], "tab ID")
    navigation = page["navigation"]
    if not isinstance(navigation, str) or not TOKEN.fullmatch(navigation):
        raise ValueError("Invalid navigation token")
    origin, domain = origin_url(page["origin"])
    title = page["title"]
    if not isinstance(title, str) or len(title) > 2048 or "\x00" in title:
        raise ValueError("Invalid page title")
    try:
        title.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("Invalid page title encoding") from error
    media = page["media"]
    if not isinstance(media, dict) or set(media) != {"detected", "playing", "age_ms"}:
        raise ValueError("Invalid media observation")
    if type(media["detected"]) is not bool or type(media["playing"]) is not bool:
        raise ValueError("Invalid media evidence")
    age = integer(media["age_ms"], "media age", 0, 86400_000)
    playing = media["detected"] and media["playing"] and age <= 2500
    return BrowserSnapshot(sequence, timestamp, focused, count, window_id, tab_id, navigation,
                           origin, domain, title, playing, age)


def envelope(value: dict[str, object]) -> tuple[str, str, object]:
    if set(value) != {"version", "origin", "stream", "snapshot"} or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported bridge envelope")
    origin, stream = value["origin"], value["stream"]
    if not isinstance(origin, str) or not re.fullmatch(r"chrome-extension://[a-p]{32}/", origin):
        raise ValueError("Invalid extension origin")
    if not isinstance(stream, str) or not TOKEN.fullmatch(stream):
        raise ValueError("Invalid stream token")
    return origin, stream, value["snapshot"]
