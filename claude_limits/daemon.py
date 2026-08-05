#!/usr/bin/env python3
"""BUSY Bar limits tracker daemon.

Single fast-ticking loop:
  * Data (usage + counts) is refreshed on a slower cadence
    (``cfg.poll_seconds``); on each successful refresh the full dashboard frame
    (text + Clawd mascot) is pushed once.
  * Every tick the animated Clawd mascot rects are re-pushed from the freshest
    snapshot, giving a smooth ~4 fps animation between polls.

Run as ``python3 -m claude_limits.daemon``. Emits JSON-lines logs to stdout.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from claude_limits.config import load
from claude_limits.credentials import get_access_token, AuthError
from claude_limits.usage import fetch_usage, UsageSnapshot, UsageError
from claude_limits.agents import read_counts
from claude_limits.busybar import BusyBarClient, DeviceError, DRAW_PATH, APP_NAME
from claude_limits.device_mode import DeviceModeListener
from claude_limits.render import (
    build_elements,
    animation_for,
    frame_elements,
    STALE_SECONDS,
)

MAX_BACKOFF = 300  # seconds, device-unreachable backoff cap

ANIM_INTERVAL = 0.25  # ~4 fps mascot re-push

# Live data carried across ticks. Updated on each successful data refresh so
# the mascot animates from the freshest snapshot even between polls.
_SNAP = None  # type: UsageSnapshot | None

_LOG_LEVELS = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}


def _fixture(now: float) -> UsageSnapshot:
    """Mock snapshot without touching OAuth or the usage endpoint."""
    base = datetime.now(timezone.utc)
    return UsageSnapshot(
        five_pct=60.0,
        five_resets=base + timedelta(hours=2, minutes=13),
        week_pct=92.0,
        week_resets=base + timedelta(hours=29),
        fetched_at=now,
    )


class _Logger:
    """JSON-lines logger to stdout with a minimum level filter."""

    def __init__(self, level: int):
        self.level = level
        self.fields = {}

    def set_fields(self, **fields):
        self.fields = fields

    def emit(self, level: str, msg: str, **extra):
        if _LOG_LEVELS[level] < self.level:
            return
        rec = {"ts": time.time(), "msg": msg}
        rec.update(self.fields)
        rec.update(extra)
        print(json.dumps(rec), flush=True)


def main(argv=None) -> int:
    global _SNAP

    ap = argparse.ArgumentParser(prog="claude_limits.daemon")
    ap.add_argument("--once", action="store_true", help="run a single cycle")
    ap.add_argument("--mock", action="store_true", help="use the fixture frame")
    ap.add_argument(
        "--log-level", default="INFO", help="DEBUG|INFO|WARNING|ERROR (default INFO)"
    )
    args = ap.parse_args(argv)

    level = _LOG_LEVELS.get(args.log_level.upper(), _LOG_LEVELS["INFO"])
    log = _Logger(level)

    cfg = load()
    os.makedirs(cfg.state_dir, mode=0o700, exist_ok=True)
    client = BusyBarClient(cfg)

    # --once is a synchronous smoke-test path that returns from inside the
    # slow branch below; gating it would hang on almost every invocation
    # since the gate is closed for the first several seconds after connect.
    mode_listener = None
    if cfg.device_mode_gate_enabled and not args.once:
        mode_listener = DeviceModeListener(cfg, log=log.emit)

    last_good = None  # type: UsageSnapshot | None
    backoff = 0
    last_data = 0.0  # when the last data refresh ran (epoch seconds)
    device_active = True  # last-observed device-mode gate state
    force_redraw = False  # set on the inactive->active edge for a cheap resume


    def build_full(snap, now, frame):
        """Build the full dashboard frame for the given snapshot/frame.

        Refreshes live counts, stamps the JSON log fields, then
        ``build_elements`` (which also emits the Clawd rects)."""
        counts = read_counts(cfg)
        log.set_fields(
            five=getattr(snap, "five_pct", None),
            week=getattr(snap, "week_pct", None),
            agents=counts.agents,
            agents_raw=counts.raw_agents,
            sessions=counts.sessions,
        )
        return build_elements(snap, counts, cfg, now, frame)


    while True:
        now = time.time()

        # ---- device-mode gate: only draw in custom/apps mode ----
        active = mode_listener.is_active() if mode_listener else True
        if active != device_active:
            device_active = active
            if active:
                log.emit("INFO", "device mode active, resuming")
                force_redraw = True
            else:
                log.emit("INFO", "device mode inactive, pausing")
                try:
                    client.clear()
                except DeviceError as exc:
                    log.emit("WARNING", "clear on pause failed: " + str(exc))
        if not active:
            time.sleep(1.0)
            continue

        # ---- data refresh (slow cadence; owns all fetch/retry/error/backoff) ----
        poll_due = now - last_data >= cfg.poll_seconds
        if poll_due or force_redraw:
            # Cheap resume: rebuild from the cached snapshot instead of
            # re-hitting the usage/auth endpoints, unless a real poll is due
            # anyway or there's no snapshot yet (very first cycle).
            cheap_redraw = force_redraw and not poll_due and _SNAP is not None
            force_redraw = False
            if cheap_redraw:
                snap = _SNAP
            elif args.mock:
                snap = _fixture(now)
                last_good = snap
            else:
                try:
                    token = get_access_token(cfg)
                except AuthError as exc:
                    log.emit("WARNING", "auth error: " + str(exc))
                    snap = None
                else:
                    try:
                        snap = fetch_usage(cfg, token)
                        last_good = snap
                    except AuthError as exc:
                        log.emit("WARNING", "auth error (usage): " + str(exc))
                        snap = None
                    except UsageError as exc:
                        log.emit("WARNING", "usage error, retrying in 30s: " + str(exc))
                        time.sleep(30)
                        try:
                            snap = fetch_usage(cfg, token)
                            last_good = snap
                        except AuthError as exc2:
                            log.emit("WARNING", "auth error (usage retry): " + str(exc2))
                            snap = None
                        except UsageError as exc2:
                            # Fall back to last good snapshot if any;
                            # build_elements derives stale-state when it has
                            # aged past STALE_SECONDS. Without any good
                            # snapshot, show a stale frame — a usage failure
                            # must never masquerade as an auth problem.
                            log.emit("WARNING", "usage error after retry: " + str(exc2))
                            if last_good is None:
                                base = datetime.now(timezone.utc)
                                snap = UsageSnapshot(
                                    five_pct=0.0,
                                    five_resets=base + timedelta(hours=1),
                                    week_pct=0.0,
                                    week_resets=base + timedelta(days=1),
                                    fetched_at=now - STALE_SECONDS - 60,
                                )
                            else:
                                snap = last_good
                    except Exception as exc:
                        log.emit("WARNING", "refresh error: " + str(exc))
                        snap = last_good

            _SNAP = snap
            frame = animation_for(snap, now, cfg)
            elements = build_full(snap, now, frame)

            try:
                status = client.draw(elements)
            except DeviceError as exc:
                backoff = 30 if backoff == 0 else min(backoff * 2, MAX_BACKOFF)
                log.emit("ERROR", "device unreachable: " + str(exc), draw=None)
                if args.once:
                    return 1
                time.sleep(backoff)
                continue

            # Success (any HTTP status received) resets the unreachable backoff.
            backoff = 0

            if status == 200:
                log.emit("INFO", "cycle", draw=200)
            elif status == 409:
                log.emit("WARNING", "busy session active, backing off", draw=409)
            elif status == 401:
                log.emit("WARNING", "bad api_token, retrying", draw=401)
            elif status == 400:
                _, body = client.draw_capture(elements)
                log.emit("ERROR", "draw rejected (layout bug): " + body, draw=400)
            else:
                log.emit("WARNING", "draw unexpected status", draw=status)

            if args.once:
                return 0

            if not cheap_redraw:
                last_data = time.time()

        # ---- every tick: re-push the animated mascot rects ----
        now = time.time()
        frame = animation_for(_SNAP, now, cfg)
        try:
            status = client.draw(frame_elements(frame, cfg))
        except DeviceError:
            # Best-effort mascot push; the data-refresh branch above owns
            # device-unreachable backoff/retry handling.
            pass
        else:
            log.emit("DEBUG", "fast tick", draw=status, anim=frame.anim)

        time.sleep(ANIM_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
