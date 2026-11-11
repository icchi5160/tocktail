"""Native Messaging stdin/stdout endpoint; stdout contains framed JSON only."""

import json
import struct
import sys
import time
from typing import BinaryIO, Protocol
from collections.abc import Callable
from uuid import uuid4

from tocktail.browser.config import BridgeConfig
from tocktail.browser.matching import BrowserIdentity
from tocktail.browser.protocol import MAX_MESSAGE, decode_json, snapshot


class Forwarder(Protocol):
    def send(self, data: bytes) -> None: ...
    def close(self) -> None: ...


def read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def read_message(stream: BinaryIO) -> bytes | None:
    header = read_exact(stream, 4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Truncated Native Messaging header")
    size = struct.unpack("<I", header)[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Invalid Native Messaging message size")
    data = read_exact(stream, size)
    if len(data) != size:
        raise ValueError("Truncated Native Messaging body")
    return data


def write_message(stream: BinaryIO, value: object) -> None:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if not 0 < len(data) <= MAX_MESSAGE:
        raise ValueError("Invalid Native Messaging response size")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def relay(input_stream: BinaryIO, output_stream: BinaryIO, *, origin: str, config: BridgeConfig,
          identity: BrowserIdentity | None, client: Forwarder,
          now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000) -> None:
    if identity is None or not config.allows(identity.family, origin):
        raise ValueError("Native Messaging caller is not allowed")
    token = uuid4().hex
    previous_sequence = -1
    previous_status = None
    try:
        while (data := read_message(input_stream)) is not None:
            value = decode_json(data)
            parsed = snapshot(value, now_ms())
            if parsed.sequence <= previous_sequence:
                raise ValueError("Native Messaging sequence must increase")
            previous_sequence = parsed.sequence
            payload = json.dumps({"version": 1, "origin": origin, "stream": token,
                                  "snapshot": value}, separators=(",", ":"), ensure_ascii=False).encode()
            connected = False
            try:
                client.send(payload)
                connected = True
            except OSError:
                client.close()
            if connected != previous_status:
                write_message(output_stream, {"type": "status", "desktop_connected": connected})
                previous_status = connected
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if sys.platform != "win32" or not args or any(not arg.startswith("--parent-window=") for arg in args[1:]):
        return 2
    # The service-worker parent window is normally zero and is never used as a mapping proof.
    try:
        for argument in args[1:]:
            if not argument.partition("=")[2].isdigit():
                raise ValueError("Invalid parent window argument")
        import msvcrt
        import os
        from tocktail.browser.pipe import PipeClient
        from tocktail.browser.windows import WindowsEvidence
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        config = BridgeConfig.load()
        inspector = WindowsEvidence()
        identity = inspector.browser_ancestor()
        if identity is None or not config.allows(identity.family, args[0]):
            raise ValueError("Untrusted Native Messaging caller")
        client = PipeClient(config.pipe_name(inspector.user_sid()))
        relay(sys.stdin.buffer, sys.stdout.buffer, origin=args[0], config=config, identity=identity, client=client)
    except (OSError, ValueError):
        print("Tocktail native host stopped: invalid caller, message or local configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
