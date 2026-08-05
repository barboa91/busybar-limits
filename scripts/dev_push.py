#!/usr/bin/env python3
"""One-shot BusyBar display test (developer tool).

Runs from the repo root as ``python3 scripts/dev_push.py``:

  * default (mock): build a fixture frame and push it to the device;
  * ``--clear``: delete this application's elements from the device.

Exit code is 0 when the device returns HTTP 200.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make ``claude_limits`` importable when invoked as ``python3 scripts/dev_push.py``
# from the repo root, regardless of whether the process cwd is on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_limits.agents import AgentCounts  # noqa: E402
from claude_limits.busybar import BusyBarClient  # noqa: E402
from claude_limits.config import Config, load, CONFIG_PATH  # noqa: E402
from claude_limits.render import build_elements  # noqa: E402
from claude_limits.usage import UsageSnapshot  # noqa: E402

DEVICE_URL = "http://10.0.4.20"
API_TOKEN = os.environ.get("BUSYBAR_API_TOKEN", "")


def _config() -> Config:
    """Use the real user config when present, else a fixture config for the
    predefined LAN device (publish-safe). A malformed config must NOT silently
    fall back to the device — ``load()``'s ``SystemExit`` propagates."""
    path = os.environ.get("BUSYBAR_LIMITS_CONFIG", CONFIG_PATH)
    config_path = str(Path(path).expanduser())
    if os.path.isfile(config_path):
        return load()
    return Config.defaults(device_url=DEVICE_URL, api_token=API_TOKEN)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push a fixture frame to (or clear) the BusyBar display."
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="DELETE this application's elements from the device instead of "
             "pushing a fixture frame.",
    )
    args = parser.parse_args()

    cfg = _config()
    client = BusyBarClient(cfg)

    if args.clear:
        status = client.clear()
        print(f"clear status {status}")
        return 0 if status == 200 else 1

    now = time.time()
    snap = UsageSnapshot(
        five_pct=60.0,
        five_resets=datetime.fromtimestamp(now + 2 * 3600 + 13 * 60, timezone.utc),
        week_pct=92.0,
        week_resets=datetime.fromtimestamp(now + 29 * 3600, timezone.utc),
        fetched_at=now,
    )
    counts = AgentCounts(
        raw_agents=3, raw_sessions=2, agents=3, sessions=2, claude_running=True
    )
    elements = build_elements(snap, counts, cfg, now)
    status = client.draw(elements)
    print(f"draw status {status}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
