#!/usr/bin/env python3
"""Turn the installed busybar limits launchd agent on or off.

Wraps ``launchctl load/unload -w`` on the plist written by
``install_daemon.py`` (``~/Library/LaunchAgents/com.busybar.limits.plist``),
so the daemon can be paused and resumed without reinstalling it. ``-w``
persists the enabled/disabled state across logins, matching an on/off switch
rather than a one-shot kill that ``KeepAlive`` would just respawn.

``off`` also clears the app's elements from the device immediately, instead
of leaving the last frame on screen until its ``element_timeout_seconds``
dead-man window naturally expires.

Usage: ``python3 scripts/daemon_ctl.py on|off|status``
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Make ``claude_limits`` importable when invoked as
# ``python3 scripts/daemon_ctl.py`` from the repo root, regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_limits.busybar import BusyBarClient, DeviceError  # noqa: E402
from claude_limits.config import load  # noqa: E402

LABEL = "com.busybar.limits"
DEST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def _running() -> bool:
    return subprocess.run(
        ["launchctl", "list", LABEL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _clear_device() -> None:
    try:
        cfg = load()
    except SystemExit as exc:
        print(f"warning: skipping device clear (no config): {exc}", file=sys.stderr)
        return
    try:
        status = BusyBarClient(cfg).clear()
    except DeviceError as exc:
        print(f"warning: could not clear device: {exc}", file=sys.stderr)
        return
    if status != 200:
        print(f"warning: clear returned status {status}", file=sys.stderr)


def _on() -> int:
    if not os.path.isfile(DEST):
        print(f"error: {DEST} not found -- run install_daemon.py first",
              file=sys.stderr)
        return 1
    if _running():
        print("busybar limits daemon: already on")
        return 0
    r = subprocess.run(["launchctl", "load", "-w", DEST])
    if r.returncode != 0:
        print(f"error: launchctl load failed ({r.returncode})", file=sys.stderr)
        return 1
    print("busybar limits daemon: on")
    return 0


def _off() -> int:
    if not os.path.isfile(DEST):
        print("busybar limits daemon: not installed")
        return 0
    if not _running():
        print("busybar limits daemon: already off")
        return 0
    r = subprocess.run(["launchctl", "unload", "-w", DEST])
    if r.returncode != 0:
        print(f"error: launchctl unload failed ({r.returncode})", file=sys.stderr)
        return 1
    _clear_device()
    print("busybar limits daemon: off")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("state", choices=["on", "off", "status"])
    args = ap.parse_args()

    if args.state == "on":
        return _on()
    if args.state == "off":
        return _off()
    print("on" if _running() else "off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
