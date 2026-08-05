"""Background listener for the BUSY Bar physical mode-selector state.

There's no REST endpoint for reading the device's current mode — it's only
observable on the undocumented ``GET /api/status/ws`` binary WebSocket stream
(see ``docs/busybar-ws-protocol.md``, reverse-engineered by hand; the official
``busylib`` package doesn't cover this endpoint either). This is a stdlib-only
WebSocket client (``socket``/``struct``/``threading``, no new pip dependency —
system Python here is externally-managed and blocks a bare ``pip install``
anyway), adapted from the reference capture script ``docs/ws_listen.py``.

The mode-selector status frame (tag ``12``, see below) is emitted only when
the selector moves, not periodically while it sits still — a 45s capture
with the selector parked untouched on custom produced zero mode-selector
frames (confirmed against live hardware, contradicting the "every 5-7s"
description in ``docs/busybar-ws-protocol.md``, which was observing
continuous-rotation test traffic, not steady state). So the last observed
mode is trusted indefinitely while connected; there is no way to distinguish
"still in custom, nothing to report" from "went silent" using this field
alone. One consequence: a switch specifically to ``busy`` can't be detected
this way either, since proto3 zero-value elision means busy never gets an
explicit frame in the first place — see :data:`MODE_BUSY`.

:class:`DeviceModeListener` runs the connection on a background daemon
thread and exposes :meth:`DeviceModeListener.is_active` for the daemon's main
loop to poll each tick.
"""

import base64
import os
import socket
import struct
import threading
import time
from urllib.parse import urlsplit

from claude_limits.config import Config

WS_PATH = "/api/status/ws"
ENABLE_MSG = b'{"enable": true}'

# Mode-selector values observed on the wire (local to this stream, not the
# `/api/input` REST enum order) -- see docs/busybar-ws-protocol.md.
MODE_BUSY = 0
MODE_CUSTOM = 1
MODE_OFF = 2
MODE_APPS = 3
MODE_SETTINGS = 4
ACTIVE_MODES = {MODE_CUSTOM, MODE_APPS}

# 17-byte mode-selector status frame, timestamp-prefix stripped:
# 12 06 5a 04 12 02 08 <mode>
_MODE_FRAME_LEN = 17
_MODE_FRAME_HEADER = b"\x12\x06\x5a\x04\x12\x02\x08"

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 2  # lets the read loop notice ping/pong promptly

# Heartbeats arrive "dozens/sec" per docs/busybar-ws-protocol.md, independent
# of mode -- so total silence for this long means the socket itself is dead
# (e.g. a silent network partition with no FIN/RST), not just a quiet mode.
# Without this, a dead-but-not-closed socket would sit in
# `except socket.timeout: continue` forever, `_connected` would never flip to
# False, and is_active() would latch on the last-known mode forever instead
# of failing open -- exactly what fail-open exists to prevent.
LINK_STALL_SECONDS = 5


def _parse_mode(payload: bytes):
    """Return the mode byte if ``payload`` is a mode-selector status frame,
    else ``None``."""
    if len(payload) != _MODE_FRAME_LEN or payload[0] != 0x09:
        return None
    if payload[9:16] != _MODE_FRAME_HEADER:
        return None
    return payload[16]


class _BufferedSocket:
    """Minimal buffered-recv wrapper so handshake leftovers feed frame reads."""

    def __init__(self, sock, initial=b""):
        self.sock = sock
        self.leftover = initial

    def recv_exact(self, n):
        buf = self.leftover[:n]
        self.leftover = self.leftover[n:]
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("device-mode ws: socket closed")
            buf += chunk
        return buf

    def sendall(self, data):
        self.sock.sendall(data)


def _send_frame(bsock, payload: bytes, opcode=0x1):
    """Write one masked client WS frame (RFC6455 requires client masking)."""
    mask = os.urandom(4)
    length = len(payload)
    header = bytearray()
    header.append(0x80 | opcode)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    header += mask
    masked = bytearray(payload)
    for i in range(len(masked)):
        masked[i] ^= mask[i % 4]
    bsock.sendall(bytes(header) + bytes(masked))


def _read_frame(bsock):
    b1, b2 = bsock.recv_exact(2)
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", bsock.recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", bsock.recv_exact(8))[0]
    mask_key = bsock.recv_exact(4) if masked else None
    payload = bsock.recv_exact(length) if length else b""
    if mask_key:
        payload = bytes(payload[i] ^ mask_key[i % 4] for i in range(len(payload)))
    return opcode, payload


def _handshake(sock, host: str, path: str) -> _BufferedSocket:
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("device-mode ws: closed during handshake")
        resp += chunk
    header_part, _, rest = resp.partition(b"\r\n\r\n")
    if b"101" not in header_part.split(b"\r\n")[0]:
        raise ConnectionError(f"device-mode ws: handshake failed: {header_part[:200]!r}")
    return _BufferedSocket(sock, initial=rest)


def _ws_target(device_url: str):
    parts = urlsplit(device_url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port


class DeviceModeListener:
    """Background WS client tracking the device's physical mode-selector.

    Fails open: :meth:`is_active` returns ``True`` (keep drawing) whenever
    the listener has never connected, the WS link is currently down, or no
    mode-selector frame has been observed yet on this connection -- there's
    no REST readback of current mode, and (see module docstring) the device
    doesn't proactively announce it either, so a fresh connection has no way
    to learn the mode already in effect before the selector next moves. Once
    a real mode frame has been seen, it's trusted as-is (again, no
    staleness check -- see module docstring). A wifi hiccup must not be
    treated the same as the user deliberately switching modes.
    """

    def __init__(self, cfg: Config, log=None):
        self._host, self._port = _ws_target(cfg.device_url)
        self._backoff_max = cfg.device_mode_ws_backoff_max_seconds
        self._log = log  # optional callable(level: str, msg: str)
        self._stall_seconds = LINK_STALL_SECONDS

        self._lock = threading.Lock()
        self._connected = False
        self._last_mode = None

        self._thread = threading.Thread(
            target=self._run, name="device-mode-ws", daemon=True
        )
        self._thread.start()

    def is_active(self) -> bool:
        with self._lock:
            connected = self._connected
            mode = self._last_mode
        if not connected or mode is None:
            return True
        return mode in ACTIVE_MODES

    def _log_emit(self, level: str, msg: str):
        if self._log is not None:
            self._log(level, msg)

    def _run(self):
        # Per-outage backoff: reset to 1 on every successful (re)connect
        # inside _connect_and_listen, so a blip after a long outage doesn't
        # inherit the prior outage's fully-ramped delay.
        self._backoff = 1
        while True:
            try:
                self._connect_and_listen()
            except Exception as exc:
                self._log_emit(
                    "WARNING", f"device-mode ws error, reconnecting: {exc}"
                )
            with self._lock:
                self._connected = False
            time.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, self._backoff_max)

    def _connect_and_listen(self):
        sock = socket.create_connection(
            (self._host, self._port), timeout=CONNECT_TIMEOUT
        )
        try:
            bsock = _handshake(sock, self._host, WS_PATH)
            _send_frame(bsock, ENABLE_MSG)
            sock.settimeout(READ_TIMEOUT)
            with self._lock:
                self._connected = True
            self._backoff = 1
            last_frame = time.time()

            while True:
                try:
                    opcode, payload = _read_frame(bsock)
                except socket.timeout:
                    if time.time() - last_frame > self._stall_seconds:
                        raise ConnectionError(
                            f"device-mode ws: no frames for "
                            f"{self._stall_seconds}s, link presumed dead"
                        )
                    continue
                last_frame = time.time()
                if opcode == 0x8:  # close
                    return
                if opcode == 0x9:  # ping
                    _send_frame(bsock, payload, opcode=0xA)
                    continue
                if opcode == 0x2:  # binary
                    mode = _parse_mode(payload)
                    if mode is not None:
                        with self._lock:
                            self._last_mode = mode
        finally:
            sock.close()
