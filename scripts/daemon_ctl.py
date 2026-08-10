#!/usr/bin/env python3
"""Turn the installed busybar limits systemd user service on or off.

Wraps ``systemctl --user start/enable`` and ``stop/disable`` on the unit
written by ``install_daemon.py``
(``~/.config/systemd/user/busybar-limits.service``), so the daemon can be
paused and resumed without reinstalling it. Disabling (not just stopping)
matches launchd's ``unload -w``: the paused state survives a logout/login,
rather than the unit's ``Restart=always``/``WantedBy=default.target``
bringing it back at the next login.

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

LABEL = "busybar-limits.service"
DEST = os.path.expanduser(f"~/.config/systemd/user/{LABEL}")


def _running() -> bool:
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", LABEL],
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
    r = subprocess.run(["systemctl", "--user", "enable", "--now", LABEL])
    if r.returncode != 0:
        print(f"error: systemctl enable --now failed ({r.returncode})", file=sys.stderr)
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
    r = subprocess.run(["systemctl", "--user", "disable", "--now", LABEL])
    if r.returncode != 0:
        print(f"error: systemctl disable --now failed ({r.returncode})", file=sys.stderr)
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
