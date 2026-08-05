"""Fetch Claude usage from Anthropic's OAuth endpoint."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from claude_limits.config import Config
from claude_limits.credentials import AuthError

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TIMEOUT = 15


@dataclass
class UsageSnapshot:
    five_pct: float
    five_resets: datetime
    week_pct: float
    week_resets: datetime
    fetched_at: float


class UsageError(Exception):
    pass


def _parse_resets(raw: str) -> datetime:
    """Parse a resets_at timestamp into a tz-aware UTC datetime (Py3.9-safe)."""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_usage(cfg: Config, token: str) -> UsageSnapshot:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": cfg.user_agent,
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError(f"usage HTTP {exc.code}")
        if exc.code == 429:
            raise UsageError("rate limited")
        raise UsageError(f"usage HTTP {exc.code}")
    except Exception as exc:
        raise UsageError(f"usage request failed: {exc}")

    try:
        five = data["five_hour"]
        week = data["seven_day"]
        return UsageSnapshot(
            five_pct=float(five["utilization"]),
            five_resets=_parse_resets(five["resets_at"]),
            week_pct=float(week["utilization"]),
            week_resets=_parse_resets(week["resets_at"]),
            fetched_at=time.time(),
        )
    except (KeyError, TypeError, ValueError):
        raise UsageError("unexpected payload")
