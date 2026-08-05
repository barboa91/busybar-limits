#!/usr/bin/env python3
"""Busy Bar limits hook — record Claude Code session/subagent lifecycle events.

Invoked by Claude Code via ``/usr/bin/python3 <abs path>`` from the ``hooks``
config in ``~/.claude/settings.json``. Reads a single JSON object from stdin and
updates ``~/.local/share/busybar-limits/agents.json`` for the daemon to consume.

Correctness contract:
- ALWAYS exits 0 (a failing hook must never block Claude Code).
- NULL input (empty/partial/non-JSON) is tolerated silently — never raise.
- The whole read-modify-write runs under an exclusive ``fcntl`` lock on
  ``agents.lock``; state is written via a temp file + ``os.replace`` so readers
  never observe partially-written JSON.
- State dir is created ``0o700``. ``last_event`` is refreshed on every write.
- Sessions/agents dicts are capped at MAX_ENTRIES, dropping oldest by ``started``.

Python 3.9-compatible stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import time

# Resolve the repo root from this file so the hook works from any checkout.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_limits.config import DEFAULT_STATE_DIR

STATE_DIR = os.path.expanduser(DEFAULT_STATE_DIR)
STATE_PATH = os.path.join(STATE_DIR, "agents.json")
LOCK_PATH = os.path.join(STATE_DIR, "agents.lock")
MAX_ENTRIES = 200


def _get_entries(state: dict, key: str) -> dict:
    """Return ``state[key]`` as a dict, creating/replacing it as needed."""
    value = state.get(key)
    if not isinstance(value, dict):
        value = {}
        state[key] = value
    return value


def _started(entry) -> float:
    """``started`` as a float; unusable stamps sort as stale (oldest)."""
    try:
        return float(entry["started"])
    except (KeyError, TypeError, ValueError):
        return float("inf")


def _drop_oldest(entries: dict, limit: int) -> None:
    """Evict entries (oldest by ``started`` first) until ``len <= limit``."""
    if len(entries) <= limit:
        return
    ordered = sorted(entries.items(), key=lambda kv: _started(kv[1]))
    for key, _ in ordered[: len(entries) - limit]:
        entries.pop(key, None)


def _handle_session_start(sessions, agents, event) -> None:  # noqa: ARG001
    sid = event.get("session_id")
    if not sid:
        return
    sessions[sid] = {"started": time.time(), "cwd": event.get("cwd")}


def _handle_session_end(sessions, agents, event) -> None:
    sid = event.get("session_id")
    if not sid:
        return
    sessions.pop(sid, None)
    for aid in [aid for aid, a in agents.items() if a.get("session_id") == sid]:
        agents.pop(aid, None)


def _handle_subagent_start(sessions, agents, event) -> None:  # noqa: ARG001
    aid = event.get("agent_id")
    if not aid:
        return
    agents[aid] = {
        "session_id": event.get("session_id"),
        "type": event.get("agent_type"),
        "started": time.time(),
    }


def _handle_subagent_stop(sessions, agents, event) -> None:  # noqa: ARG001
    aid = event.get("agent_id")
    if aid:
        agents.pop(aid, None)


_HANDLERS = {
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
    "SubagentStart": _handle_subagent_start,
    "SubagentStop": _handle_subagent_stop,
}


def _read_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(state: dict) -> None:
    """Write ``state`` to a temp file in the same dir, then atomically replace."""
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, prefix="agents.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, STATE_PATH)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _apply(event: dict) -> None:
    name = event.get("hook_event_name")
    handler = _HANDLERS.get(name)
    if handler is None:
        return
    with open(LOCK_PATH, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = _read_state()
            sessions = _get_entries(state, "sessions")
            agents = _get_entries(state, "agents")
            handler(sessions, agents, event)
            _drop_oldest(sessions, MAX_ENTRIES)
            _drop_oldest(agents, MAX_ENTRIES)
            state["last_event"] = "%s @%d" % (name, int(time.time()))
            _atomic_write(state)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main() -> None:
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    try:
        data = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError):
        return
    if isinstance(data, dict):
        _apply(data)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A hook must never break Claude Code.
        pass
