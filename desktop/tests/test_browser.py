from dataclasses import replace
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import struct

import pytest

from tocktail.browser.bridge import BrowserBridge
from tocktail.browser.config import BridgeConfig
from tocktail.browser.matching import BrowserIdentity, NativeWindow, WindowEvidence, browser_root
from tocktail.browser.native_host import read_message, relay, write_message
from tocktail.browser.pipe import Mailbox, Received
from tocktail.browser.protocol import MAX_MESSAGE, decode_json, domain_name, origin_url, snapshot
from tocktail.browser.registration import host_manifest, install_bundle, prepare_bundle, unregister
from tocktail.browser.report import domain_usage
from tocktail.config.settings import Settings
from tocktail.core.models import ActivityState as State, Application
from tocktail.runtime.service import TrackingService
from tocktail.storage.sqlite import Store


EXTENSION = "a" * 32
ORIGIN = f"chrome-extension://{EXTENSION}/"
STREAM = "b" * 32
BASE = 1_800_000_000_000


def message(sequence=1, timestamp=BASE, **overrides):
    value = {"version": 1, "sequence": sequence, "timestamp_ms": timestamp, "focused": True,
             "window_count": 1, "page": {"window_id": 777, "tab_id": 111, "navigation": "c" * 32,
                "origin": "https://example.com", "title": "Example",
                "media": {"detected": True, "playing": True, "age_ms": 0}}}
    value.update(overrides)
    return value


def frame(value):
    body = json.dumps(value, ensure_ascii=False).encode()
    return struct.pack("<I", len(body)) + body


@pytest.mark.parametrize("url", [
    "https://name:secret@example.com", "https://example.com/private", "https://example.com/?q=secret",
    "https://example.com/#token", "file:///c:/secret", "chrome://history", "javascript:alert(1)",
    "https://exam\nple.com", "https://example.com:99999", "https://example.com\\evil",
])
def test_origin_rejects_private_url_components(url):
    with pytest.raises(ValueError):
        origin_url(url)


def test_domain_and_origin_normalization():
    assert domain_name(" EXAMPLE.COM. ") == "example.com"
    assert origin_url("https://EXAMPLE.com:443/") == ("https://example.com:443", "example.com")
    assert domain_name("例え.jp").startswith("xn--")


@pytest.mark.parametrize("data", [b"[]", b"null", b"{", b'\xff', b'{"x":1,"x":2}', b'{"x":NaN}', b"" , b"x" * (MAX_MESSAGE + 1)])
def test_invalid_json_is_rejected(data):
    with pytest.raises(ValueError):
        decode_json(data)


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"focused": 1}, {"window_count": -1}, {"sequence": True},
    {"timestamp_ms": BASE - 4001}, {"timestamp_ms": BASE + 1001}, {"unexpected_input": "secret"},
])
def test_invalid_snapshot_is_rejected(change):
    with pytest.raises(ValueError):
        snapshot(message(**change), BASE)


def test_media_requires_fresh_explicit_detection():
    value = message()
    assert snapshot(value, BASE).media_playing
    value["page"]["media"]["detected"] = False
    assert not snapshot(value, BASE).media_playing
    value["page"]["media"].update(detected=True, age_ms=2501)
    assert not snapshot(value, BASE).media_playing
    assert snapshot(message(page=None), BASE).domain is None


class Inspector:
    def __init__(self):
        self.identity = BrowserIdentity("chrome", 20)
        self.evidence = WindowEvidence(10, "Example - Google Chrome", (NativeWindow(10, 20),))

    def browser_ancestor(self, pid):
        return self.identity

    def window_evidence(self, observation):
        return self.evidence


class Server:
    def __init__(self):
        self.mailbox = Mailbox()
        self.closed = False

    def drain(self):
        return self.mailbox.drain()

    def close(self):
        self.closed = True


def browser_observation(observation, seconds=0, **kwargs):
    return observation(seconds, application=Application("chrome.exe", "c:/chrome.exe"), **kwargs)


def deliver(server, value=None, seconds=0, connection="connection", slot=0, **fields):
    envelope = {"version": 1, "origin": ORIGIN, "stream": STREAM,
                "snapshot": message(sequence=int(seconds * 10) + 1, timestamp=BASE + int(seconds * 1000)) if value is None else value}
    envelope.update(fields)
    server.mailbox.put(Received(slot, connection, 999, json.dumps(envelope).encode(), int(seconds * 1000)))


def bridge():
    server, inspector = Server(), Inspector()
    return BrowserBridge(BridgeConfig(Path.cwd() / "unused", (EXTENSION,)), server, inspector), server, inspector


def verified(bridge, server, observation, **kwargs):
    deliver(server)
    assert bridge.observe(browser_observation(observation), Settings())[0].content is None
    deliver(server, seconds=1)
    return bridge.observe(browser_observation(observation, 1, **kwargs), Settings())[0]


def test_single_verified_window_is_correlated_without_equating_ids(observation):
    subject, server, _ = bridge()
    result = verified(subject, server, observation)
    assert result.content.domain == "example.com"
    assert result.content.title == ""
    assert result.browser_window_id == 777 and result.hwnd == 10
    assert result.browser_tab_id == 111
    assert result.media_playing and result.content_verified


def test_edge_uses_the_same_protocol_with_separate_origin_allowlist(observation):
    subject, server, inspector = bridge()
    subject.config = BridgeConfig(Path.cwd() / "unused", edge_ids=(EXTENSION,))
    inspector.identity = BrowserIdentity("edge", 20)
    inspector.evidence = replace(inspector.evidence, title="Example - Microsoft Edge")
    for second in (0, 1):
        deliver(server, seconds=second)
        result, _ = subject.observe(observation(second, application=Application("msedge.exe", "c:/msedge.exe")), Settings())
    assert result.content.domain == "example.com"
    assert not subject.config.allows("chrome", ORIGIN)


def test_navigation_event_survives_conservative_fallback_gap(tmp_path, observation):
    subject, server, _ = bridge()
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        for second in (0, 1, 2, 3):
            value = message(sequence=second + 1, timestamp=BASE + second * 1000)
            if second >= 2:
                value["page"]["navigation"] = "e" * 32
            deliver(server, value, seconds=second)
            result, events = subject.observe(browser_observation(observation, second), service.settings)
            service.observe(result, events)
        service.close()
        types = [row[0] for row in store.connection.execute("SELECT event_type FROM activity_events")]
        assert types.count("BROWSER_TAB_CHANGED") == 1
        assert types.count("BROWSER_NAVIGATED") == 1


def test_domain_exclusion_matches_labels_not_arbitrary_suffixes():
    settings = Settings(excluded_domains=("Example.COM.",))
    assert settings.excludes_domain("example.com")
    assert settings.excludes_domain("www.example.com")
    assert not settings.excludes_domain("notexample.com")


@pytest.mark.parametrize("variant", ["multiple_native", "different_pid", "different_hwnd", "title_mismatch", "incomplete", "other_browser"])
def test_uncertain_mapping_always_falls_back(observation, variant):
    subject, server, inspector = bridge()
    verified(subject, server, observation)
    if variant == "multiple_native":
        inspector.evidence = replace(inspector.evidence, windows=(NativeWindow(10, 20), NativeWindow(11, 20)))
    elif variant == "different_pid":
        inspector.identity = BrowserIdentity("chrome", 30)
        deliver(server, seconds=2, connection="new")
    elif variant == "different_hwnd":
        inspector.evidence = replace(inspector.evidence, foreground_hwnd=11)
    elif variant == "title_mismatch":
        inspector.evidence = replace(inspector.evidence, title="Another page - Google Chrome")
    elif variant == "incomplete":
        inspector.evidence = replace(inspector.evidence, complete=False)
    elif variant == "other_browser":
        inspector.evidence = replace(inspector.evidence, title="Example - Microsoft Edge")
    deliver(server, seconds=2, connection="new" if variant == "different_pid" else "connection")
    result, _ = subject.observe(browser_observation(observation, 2), Settings())
    assert result.content is None and not result.media_playing


@pytest.mark.parametrize("change", [{"focused": False}, {"window_count": 2}, {"page": None}])
def test_browser_unfocused_multiwindow_or_restricted_page_falls_back(observation, change):
    subject, server, _ = bridge()
    verified(subject, server, observation)
    deliver(server, message(sequence=30, timestamp=BASE + 2000, **change), seconds=2)
    assert subject.observe(browser_observation(observation, 2), Settings())[0].content is None


@pytest.mark.parametrize("family,suffix", [("chrome", "Google Chrome"), ("edge", "Microsoft Edge")])
def test_multiple_windows_unique_foreground_title_is_verified(observation, family, suffix):
    subject, server, inspector = bridge()
    subject.config = BridgeConfig(Path.cwd() / "unused", **{family + "_ids": (EXTENSION,)})
    inspector.identity = BrowserIdentity(family, 20)
    inspector.evidence = WindowEvidence(10, f"Example - {suffix}", (
        NativeWindow(11, 20, f"Other page - {suffix}"),
        NativeWindow(10, 20, f"Example - {suffix}"),
        NativeWindow(12, 30, f"Other profile - {suffix}"),
    ))
    for second in (0, 1):
        deliver(server, message(sequence=second + 1, timestamp=BASE + second * 1000, window_count=2), seconds=second)
        result, _ = subject.observe(observation(second, application=Application(
            "chrome.exe" if family == "chrome" else "msedge.exe", "c:/browser.exe")), Settings())
    assert result.content_verified and result.content.domain == "example.com"
    assert result.browser_window_id != result.hwnd


@pytest.mark.parametrize("other_pid,title,count", [
    (20, "Example - Google Chrome", 2),  # Duplicate title, same browser.
    (30, "Example - Google Chrome", 1),  # Duplicate title, another root/profile.
    (20, None, 2),                       # Missing title evidence.
    (20, "", 2),                         # Unreadable title.
    (20, "Other - Google Chrome", 3),    # Extension inventory exceeds native evidence.
    (30, "Other - Google Chrome", 2),    # Extra window belongs to another root.
])
def test_multiwindow_ambiguous_or_inconsistent_evidence_falls_back(observation, other_pid, title, count):
    subject, server, inspector = bridge()
    inspector.evidence = WindowEvidence(10, "Example - Google Chrome", (
        NativeWindow(10, 20, "Example - Google Chrome"), NativeWindow(11, other_pid, title)))
    for second in (0, 1):
        deliver(server, message(sequence=second + 1, timestamp=BASE + second * 1000, window_count=count), seconds=second)
        result, _ = subject.observe(browser_observation(observation, second), Settings())
        assert result.content is None and not result.media_playing


def test_multiwindow_switch_and_duplicate_title_clear_content_without_overlap(tmp_path, observation):
    subject, server, inspector = bridge()
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        for second in range(7):
            switched = second >= 3
            hwnd = 11 if switched else 10
            title = "Other" if switched else "Example"
            other_title = "Other" if second == 6 else "Example"
            inspector.evidence = WindowEvidence(hwnd, f"{title} - Google Chrome", (
                NativeWindow(10, 20, f"{other_title} - Google Chrome"),
                NativeWindow(11, 20, "Other - Google Chrome")))
            value = message(sequence=second + 1, timestamp=BASE + second * 1000, window_count=2)
            if switched:
                value["page"].update(window_id=888, tab_id=222, title=title,
                                     origin="https://other.example", navigation="d" * 32)
            deliver(server, value, seconds=second)
            result, events = subject.observe(browser_observation(observation, second, hwnd=hwnd), service.settings)
            service.observe(result, events)
            if second in (0, 3, 6):
                assert result.content is None
            else:
                assert result.content.domain == ("other.example" if switched else "example.com")
        service.close()
        day = datetime.fromtimestamp(BASE / 1000).date()
        assert dict(domain_usage(store.connection, day)) == {"example.com": 2000, "other.example": 2000}
        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions a JOIN sessions b ON a.id<b.id "
            "WHERE a.started_at<b.ended_at AND b.started_at<a.ended_at"
        ).fetchone()[0] == 0


def test_expired_stream_and_media_cannot_be_used(observation):
    subject, server, _ = bridge()
    verified(subject, server, observation)
    result, _ = subject.observe(browser_observation(observation, 4), Settings())
    assert result.content is not None and not result.media_playing
    result, _ = subject.observe(browser_observation(observation, 6), Settings())
    assert result.content is None


def test_transport_delay_counts_toward_media_and_snapshot_expiry(observation):
    subject, server, _ = bridge()
    deliver(server, message(timestamp=BASE - 3000))
    subject.observe(browser_observation(observation), Settings())
    result, _ = subject.observe(browser_observation(observation, 0.3), Settings())
    assert result.content is not None and not result.media_playing
    result, _ = subject.observe(browser_observation(observation, 1.1), Settings())
    assert result.content is None


def test_disabling_title_storage_creates_private_future_content(tmp_path, observation):
    subject, server, _ = bridge()
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        for second, titles in ((0, True), (1, True), (2, False), (3, False)):
            service.configure(Settings(save_titles=titles))
            deliver(server, seconds=second)
            result, events = subject.observe(browser_observation(observation, second), service.settings)
            service.observe(result, events)
        service.close()
        contents = store.connection.execute("SELECT title FROM contents ORDER BY id").fetchall()
        assert [row[0] for row in contents] == ["Example", ""]


def test_disconnect_immediately_removes_content_and_passive_evidence(observation):
    subject, server, _ = bridge()
    verified(subject, server, observation)
    server.mailbox.put(Received(0, "connection", 999, None, 2000))
    result, events = subject.observe(browser_observation(observation, 2), Settings())
    assert result.content is None and not result.media_playing
    assert [event.event_type for event in events] == ["BROWSER_DISCONNECTED"]


def test_replay_invalidates_connection_until_reconnect(observation):
    subject, server, _ = bridge()
    verified(subject, server, observation)
    deliver(server, message(sequence=1, timestamp=BASE + 2000), seconds=2)
    assert subject.observe(browser_observation(observation, 2), Settings())[0].content is None
    deliver(server, seconds=3)
    assert subject.observe(browser_observation(observation, 3), Settings())[0].content is None
    deliver(server, seconds=4, connection="reconnected")
    subject.observe(browser_observation(observation, 4), Settings())
    deliver(server, seconds=5, connection="reconnected")
    assert subject.observe(browser_observation(observation, 5), Settings())[0].content is not None


def test_unapproved_origin_cannot_attach(observation):
    subject, server, _ = bridge()
    for second in (0, 1):
        deliver(server, seconds=second, origin="chrome-extension://" + "p" * 32 + "/")
        assert subject.observe(browser_observation(observation, second), Settings())[0].content is None


def test_conflicting_extension_connections_fall_back(observation):
    subject, server, _ = bridge()
    verified(subject, server, observation)
    deliver(server, seconds=2, slot=1, connection="second")
    assert subject.observe(browser_observation(observation, 2), Settings())[0].content is None


def test_excluded_domain_and_subdomains_do_not_persist_identity(tmp_path, observation):
    subject, server, inspector = bridge()
    settings = Settings(save_titles=True, excluded_domains=("example.com",))
    inspector.evidence = replace(inspector.evidence, title="Private page - Google Chrome")
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        service.configure(settings)
        for second in (0, 1, 2):
            value = message(sequence=second + 1, timestamp=BASE + second * 1000)
            value["page"].update(origin="https://sub.example.com", title="Private page")
            deliver(server, value, seconds=second)
            result, events = subject.observe(browser_observation(observation, second, title="Private page - Google Chrome"), settings)
            service.observe(result, events)
        assert service.engine.current.target.excluded
        service.close()
        assert store.connection.execute("SELECT count(*) FROM contents").fetchone()[0] == 0
        payload = repr([row[0] for row in store.connection.execute("SELECT payload_json FROM activity_events")])
        assert "Private page" not in payload and "sub.example.com" not in payload


def test_verified_site_active_passive_and_disconnect_idle_end_to_end(tmp_path, observation):
    subject, server, _ = bridge()
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        service.configure(Settings(idle_seconds=1))
        for second, idle in ((0, 0), (1, 0), (2, 2000), (3, 3000)):
            deliver(server, seconds=second)
            result, events = subject.observe(browser_observation(observation, second, idle_ms=idle), service.settings)
            service.observe(result, events)
        assert service.engine.current.target.state == State.PASSIVE
        server.mailbox.put(Received(0, "connection", 999, None, 4000))
        result, events = subject.observe(browser_observation(observation, 4, idle_ms=4000), service.settings)
        service.observe(result, events)
        assert service.engine.current.target.state == State.IDLE
        service.observe(browser_observation(observation, 5, idle_ms=5000))
        service.close()
        content = store.connection.execute("SELECT * FROM contents").fetchone()
        assert content["domain"] == "example.com" and content["title"] == "" and content["url"] is None
        day = datetime.fromtimestamp(BASE / 1000).date()
        assert domain_usage(store.connection, day) == (("example.com", 3000),)
        assert store.daily_summary(day).total_ms == 4000
        events = {row[0] for row in store.connection.execute("SELECT event_type FROM activity_events")}
        assert {"BROWSER_TAB_CHANGED", "MEDIA_STATE_CHANGED", "BROWSER_DISCONNECTED"} <= events


@pytest.mark.parametrize("data", [b"\x01", struct.pack("<I", 0), struct.pack("<I", MAX_MESSAGE + 1), struct.pack("<I", 5) + b"a"])
def test_bad_native_frames_are_rejected(data):
    with pytest.raises(ValueError):
        read_message(BytesIO(data))


def test_native_frame_roundtrip_utf8():
    stream = BytesIO()
    write_message(stream, {"title": "日本語"})
    stream.seek(0)
    assert decode_json(read_message(stream)) == {"title": "日本語"}
    assert read_message(stream) is None


def test_invalid_title_encoding_is_rejected_before_hashing():
    value = message()
    value["page"]["title"] = "\ud800"
    with pytest.raises(ValueError, match="encoding"):
        snapshot(value, BASE)


def test_native_framing_accepts_fragmented_pipe_reads():
    class Fragmented(BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))
    assert decode_json(read_message(Fragmented(frame(message()))))["version"] == 1


def test_native_host_utility_process_resolves_to_browser_root():
    names = {1: "python.exe", 2: "tocktail-native-host.exe", 3: "cmd.exe", 4: "chrome.exe", 5: "chrome.exe", 6: "explorer.exe"}
    parents = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6}
    assert browser_root(1, parents, names.get) == BrowserIdentity("chrome", 5)
    assert browser_root(1, {1: 2, 2: 1}, names.get) is None


def test_edge_started_from_chrome_still_uses_edge_identity():
    assert browser_root(1, {1: 2, 2: 3}, {1: "python.exe", 2: "msedge.exe", 3: "chrome.exe"}.get) == BrowserIdentity("edge", 2)


class Client:
    def __init__(self, offline=False):
        self.messages = []
        self.closed = False
        self.offline = offline

    def send(self, data):
        if self.offline:
            raise OSError("app not running")
        self.messages.append(decode_json(data))

    def close(self):
        self.closed = True


@pytest.mark.parametrize("offline", [False, True])
def test_native_relay_validates_and_forwards(offline):
    config = BridgeConfig(Path.cwd() / "db", (EXTENSION,))
    client, output = Client(offline), BytesIO()
    relay(BytesIO(frame(message())), output, origin=ORIGIN, config=config,
          identity=BrowserIdentity("chrome", 20), client=client, now_ms=lambda: BASE)
    assert client.closed
    output.seek(0)
    assert decode_json(read_message(output)) == {"type": "status", "desktop_connected": not offline}
    if not offline:
        assert client.messages[0]["origin"] == ORIGIN


def test_native_relay_rejects_wrong_caller_and_raw_url():
    config, client = BridgeConfig(Path.cwd() / "db", (EXTENSION,)), Client()
    with pytest.raises(ValueError):
        relay(BytesIO(), BytesIO(), origin=ORIGIN, config=config, identity=BrowserIdentity("edge", 20), client=client)
    value = message()
    value["page"]["origin"] = "https://example.com/?password=secret"
    with pytest.raises(ValueError):
        relay(BytesIO(frame(value)), BytesIO(), origin=ORIGIN, config=config,
              identity=BrowserIdentity("chrome", 20), client=client, now_ms=lambda: BASE)
    assert not client.messages


def test_mailbox_coalesces_updates_without_unbounded_backlog():
    mailbox = Mailbox()
    for index in range(1000):
        mailbox.put(Received(index % 8, "connection", 1, b"{}", index))
    assert len(mailbox.drain()) == 8
    assert not mailbox.drain()


class Registry:
    def __init__(self):
        self.values = {"chrome": None, "edge": None}

    def read(self, family):
        return self.values[family]

    def write(self, family, path):
        self.values[family] = path


def test_preparation_install_and_unregistration_are_scoped(tmp_path):
    executable = tmp_path / "host.exe"
    executable.touch()
    config = BridgeConfig(tmp_path / "db", (EXTENSION,), ("d" * 32,))
    bundle, target = tmp_path / "prepared", tmp_path / "user"
    prepare_bundle(bundle, config, executable)
    assert BridgeConfig.load(bundle / "browser-host.json") == config
    registry = Registry()
    install_bundle(bundle, registry, destination=target, expected_executable=executable)
    assert registry.values["chrome"] == registry.values["edge"]
    registry.values["edge"] = str(tmp_path / "someone-else.json")
    unregister(registry, destination=target)
    assert registry.values["chrome"] is None
    assert registry.values["edge"].endswith("someone-else.json")
    assert (target / "browser-host.json").exists()


def test_prepared_manifest_tampering_is_rejected(tmp_path):
    executable = tmp_path / "host.exe"
    executable.touch()
    config = BridgeConfig(tmp_path / "db", (EXTENSION,))
    prepare_bundle(tmp_path / "bundle", config, executable)
    manifest = tmp_path / "bundle/com.tocktail.bridge.json"
    value = json.loads(manifest.read_text())
    value["allowed_origins"].append("chrome-extension://" + "p" * 32 + "/")
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        install_bundle(manifest.parent, Registry(), destination=tmp_path / "target", expected_executable=executable)


def test_registration_rolls_back_on_failure(tmp_path):
    executable = tmp_path / "host.exe"
    executable.touch()
    config = BridgeConfig(tmp_path / "db", (EXTENSION,), ("d" * 32,))
    prepare_bundle(tmp_path / "bundle", config, executable)

    class Failing(Registry):
        def write(self, family, path):
            if family == "edge" and path is not None:
                raise OSError("registry failed")
            super().write(family, path)

    registry = Failing()
    with pytest.raises(OSError):
        install_bundle(tmp_path / "bundle", registry, destination=tmp_path / "target", expected_executable=executable)
    assert registry.values == {"chrome": None, "edge": None}
    assert not (tmp_path / "target/browser-host.json").exists()


def test_registration_rejects_a_substituted_executable(tmp_path):
    executable = tmp_path / "host.exe"
    executable.touch()
    other = tmp_path / "unrelated.exe"
    other.touch()
    config = BridgeConfig(tmp_path / "db", (EXTENSION,))
    prepare_bundle(tmp_path / "bundle", config, other)
    with pytest.raises(ValueError, match="does not match"):
        install_bundle(tmp_path / "bundle", Registry(), destination=tmp_path / "target", expected_executable=executable)
