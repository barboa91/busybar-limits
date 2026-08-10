#!/usr/bin/env python3
"""Portably install the busybar limits systemd user service.

Renders ``systemd/busybar-limits.service`` (a template with ``@REPO_ROOT@``
and ``@STATE_DIR@`` tokens) with this checkout's resolved paths, writes it to
``~/.config/systemd/user``, and enables + starts it via ``systemctl --user``.
``--dry-run`` prints the rendered unit without touching the system;
``--remove`` stops, disables, and deletes the installed unit.
"""
import argparse
import os
import subprocess
import sys

# Make ``claude_limits`` importable regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_limits.config import DEFAULT_STATE_DIR  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE = os.path.join(_REPO_ROOT, "systemd", "busybar-limits.service")
LABEL = "busybar-limits.service"
DEST = os.path.expanduser(f"~/.config/systemd/user/{LABEL}")


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
        print("no installed unit to remove")
        return 0
    subprocess.run(["systemctl", "--user", "disable", "--now", LABEL])
    os.unlink(DEST)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    print(f"removed {DEST}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the busybar limits systemd user service")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="print the rendered unit and exit (no writes)",
    )
    ap.add_argument(
        "--remove", action="store_true",
        help="stop, disable, and delete the installed unit instead",
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

    r = subprocess.run(["systemctl", "--user", "daemon-reload"])
    if r.returncode != 0:
        print(f"error: systemctl daemon-reload failed ({r.returncode})", file=sys.stderr)
        return 1

    r = subprocess.run(["systemctl", "--user", "enable", "--now", LABEL])
    if r.returncode != 0:
        print(f"error: systemctl enable --now failed ({r.returncode})", file=sys.stderr)
        return 1
    print(f"enabled + started {LABEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
