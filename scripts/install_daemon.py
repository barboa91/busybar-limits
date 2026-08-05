#!/usr/bin/env python3
"""Portably install the busybar limits launchd agent.

Renders ``launchd/com.busybar.limits.plist`` (a template with ``@REPO_ROOT@``
and ``@STATE_DIR@`` tokens) with this checkout's resolved paths, writes it to
``~/Library/LaunchAgents``, and loads it via ``launchctl``. ``--dry-run``
prints the rendered plist without touching the system; ``--remove`` unloads
and deletes the installed plist.
"""
import argparse
import os
import subprocess
import sys

# Make ``claude_limits`` importable regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_limits.config import DEFAULT_STATE_DIR  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE = os.path.join(_REPO_ROOT, "launchd", "com.busybar.limits.plist")
DEST = os.path.expanduser("~/Library/LaunchAgents/com.busybar.limits.plist")


def _render() -> str:
    state_dir = os.path.expanduser(DEFAULT_STATE_DIR)
    with open(_TEMPLATE, "r", encoding="utf-8") as fh:
        return (
            fh.read()
            .replace("@REPO_ROOT@", _REPO_ROOT)
            .replace("@STATE_DIR@", state_dir)
        )


def _remove() -> int:
    if not os.path.isfile(DEST):
        print("no installed plist to remove")
        return 0
    r = subprocess.run(["launchctl", "unload", "-w", DEST])
    if r.returncode != 0:
        print(f"error: launchctl unload failed ({r.returncode})", file=sys.stderr)
        return 1
    os.unlink(DEST)
    print(f"removed {DEST}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the busybar limits launchd agent")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="print the rendered plist and exit (no writes)",
    )
    ap.add_argument(
        "--remove", action="store_true",
        help="unload and delete the installed plist instead",
    )
    args = ap.parse_args()

    if args.remove:
        return _remove()

    rendered = _render()
    if args.dry_run:
        sys.stdout.write(rendered)
        return 0

    state_dir = os.path.expanduser(DEFAULT_STATE_DIR)
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    with open(DEST, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    print(f"wrote {DEST}")

    r = subprocess.run(["launchctl", "load", "-w", DEST])
    if r.returncode != 0:
        print(f"error: launchctl load failed ({r.returncode})", file=sys.stderr)
        return 1
    print("loaded com.busybar.limits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
