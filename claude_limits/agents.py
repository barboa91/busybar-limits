"""Read live Claude Code agent/session counts from the hook-maintained state file.

The companion hook (``hook/busybar_hook.py``) writes ``agents.json`` under an
exclusive ``fcntl`` lock with atomic ``os.replace``, so readers here never
observe a partial document. This module is strictly read/copy-on-read: it never
modifies or rewrites the state file.
"""

from __future__ import annotations

import json
import subprocess
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


def _claude_running() -> bool:
    """True if a ``claude`` process is alive; conservative (True) on pgrep error."""
    try:
        return subprocess.run(["pgrep", "-xq", "claude"]).returncode == 0
    except OSError:
        # pgrep missing/unusable — assume running so we never hide live agents.
        return True


def read_counts(cfg) -> AgentCounts:
    """Load and prune the state file, returning effective and raw counts.

    ``AgentCounts.agents``/``sessions`` reflect the live count only while Claude
    Code is running; otherwise they are 0 and the caller can still log the raw
    values. Never raises and never writes to disk.
    """
    now = time.time()
    state = _load_state(Path(cfg.state_dir) / "agents.json")

    sessions = {k: v for k, v in state.get("sessions", {}).items() if isinstance(v, dict)}
    agents = {k: v for k, v in state.get("agents", {}).items() if isinstance(v, dict)}

    _prune(agents, sessions, now)

    raw_agents = len(agents)
    raw_sessions = len(sessions)
    running = _claude_running()

    if running:
        return AgentCounts(raw_agents, raw_sessions, raw_agents, raw_sessions, True)
    return AgentCounts(raw_agents, raw_sessions, 0, 0, False)
