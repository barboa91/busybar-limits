"""Read live Claude Code agent/session counts from the hook-maintained state file.

The companion hook (``hook/busybar_hook.py``) writes ``agents.json`` under an
exclusive ``fcntl`` lock with atomic ``os.replace``, so readers here never
observe a partial document. This module is strictly read/copy-on-read: it never
modifies or rewrites the state file.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Pruning windows for leftovers from crashes / abandoned sessions.
AGENT_MAX_AGE_SECONDS = 6 * 3600  # agents started > 6 h ago are dropped
SESSION_MAX_AGE_SECONDS = 48 * 3600  # sessions started > 48 h ago are dropped


@dataclass
class AgentCounts:
    """Current effective (agent, session) counts plus raw pre-prune values.

    When Claude Code itself is not running we can't trust the state file, so
    ``agents``/``sessions`` collapse to 0 while the raw pre-prune counts are
    kept for logging/diagnostics.
    """

    raw_agents: int
    raw_sessions: int
    agents: int
    sessions: int
    claude_running: bool


def _load_state(path: Path) -> dict:
    """Return the parsed state dict, or an empty one on any failure.

    Missing/corrupt file must never raise — a transient read error degrades to
    zero counts, which the daemon renders as ``A0`` / ``AGENTS 0 / 0 SESS``.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _prune(agents: dict, sessions: dict, now: float) -> None:
    """Drop entries older than their pruning windows (in place, on the copy)."""
    agen = {aid: a for aid, a in agents.items() if _age(a, "started", now) <= AGENT_MAX_AGE_SECONDS}
    ssess = {sid: s for sid, s in sessions.items() if _age(s, "started", now) <= SESSION_MAX_AGE_SECONDS}
    agents.clear()
    agents.update(agen)
    sessions.clear()
    sessions.update(ssess)


def _age(entry, key: str, now: float) -> float:
    """Return age in seconds of ``entry[key]``; treat an unusable stamp as stale."""
    try:
        return now - float(entry[key])
    except (KeyError, TypeError, ValueError):
        return float("inf")



# Claude Code keeps a permanent support-process pool alive for the
# background-job feature (``daemon run``, and the ``bg-pty-host``/``bg-spare``
# spare-shell pool) independent of whether any interactive session is open —
# they match plain ``pgrep -x claude`` even with zero sessions running, which
# pinned this gate open forever. Only a bare invocation or a real session flag
# (``--resume``, ``--continue``, ...) counts as an actual interactive process.
_SUPPORT_ARGV0 = ("daemon",)
_SUPPORT_ARGV0_PREFIX = "bg-"


def _is_support_process(argv: list) -> bool:
    """True if ``argv[1:]`` (the args after the ``claude`` binary) belong to
    one of Claude Code's persistent background-job support processes rather
    than an interactive session."""
    if len(argv) < 2:
        return False
    first = argv[1]
    return first in _SUPPORT_ARGV0 or first.startswith(_SUPPORT_ARGV0_PREFIX)


def _live_session_count() -> int:
    """Count interactive ``claude`` session processes alive right now.

    Returns a conservatively large number (``sys.maxsize``, i.e. "trust the
    state file") on pgrep error or unrecognized output so we never hide live
    agents — this mirrors the old fail-open ``True`` return, just expressed
    as "don't cap" instead of a boolean.

    procps-ng's pgrep (the Linux implementation) has no short ``-q`` flag —
    only BSD/macOS pgrep accepts ``-xq`` combined — so ``--quiet`` is spelled
    out. ``-a`` lists full command lines so support processes can be filtered
    out (see module comment above); text mode + captured output avoids ever
    dumping a raw usage message if an unrecognized flag reappears.
    """
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "-a", "claude"], capture_output=True, text=True
        )
    except OSError:
        return sys.maxsize
    if proc.returncode not in (0, 1):
        # 0 = matches found, 1 = no matches; anything else is an error we
        # can't interpret, so fail open rather than hide live agents.
        return sys.maxsize
    count = 0
    for line in proc.stdout.splitlines():
        _pid, _, cmdline = line.partition(" ")
        if not _is_support_process(cmdline.split()):
            count += 1
    return count


def read_counts(cfg) -> AgentCounts:
    """Load and prune the state file, returning effective and raw counts.

    ``AgentCounts.sessions`` is capped at the number of interactive ``claude``
    processes actually alive right now, so sessions left behind by a
    non-graceful exit (killed terminal, crash — anything that skips the
    ``SessionEnd`` hook) can inflate the on-disk state file without inflating
    what gets displayed; they still age out of the file itself via
    ``_prune``. ``agents`` (subagents) can't be bounded the same way since
    several can belong to one live session, so it just collapses to 0
    alongside ``sessions`` when no session process is alive at all. Never
    raises and never writes to disk.
    """
    now = time.time()
    state = _load_state(Path(cfg.state_dir) / "agents.json")

    sessions = {k: v for k, v in state.get("sessions", {}).items() if isinstance(v, dict)}
    agents = {k: v for k, v in state.get("agents", {}).items() if isinstance(v, dict)}

    _prune(agents, sessions, now)

    raw_agents = len(agents)
    raw_sessions = len(sessions)
    live = _live_session_count()
    running = live > 0

    if not running:
        return AgentCounts(raw_agents, raw_sessions, 0, 0, False)
    return AgentCounts(raw_agents, raw_sessions, raw_agents, min(raw_sessions, live), True)
