#!/usr/bin/env python3
"""Idempotently install/remove the busybar limits hooks into ~/.claude/settings.json.

Adds a command hook to the SessionStart, SessionEnd, SubagentStart and SubagentStop
events. Preserves all other keys/groups. Writes atomically (temp + os.replace) and
backs up the file only when it is actually modified.
"""
import argparse
import json
import os
import sys
import tempfile

EVENTS = ["SessionStart", "SessionEnd", "SubagentStart", "SubagentStop"]
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMAND = f"/usr/bin/python3 {os.path.join(_REPO_ROOT, 'hook', 'busybar_hook.py')}"

SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")


def _group_has_command(group):
    """True if any entry in a group's hooks list carries the exact command."""
    for entry in group.get("hooks") or []:
        if entry.get("command") == COMMAND:
            return True
    return False


def _event_has_command(entries):
    """True if any group in the event's entries list carries the exact command."""
    for group in entries or []:
        if _group_has_command(group):
            return True
    return False


def load_settings():
    if not os.path.isfile(SETTINGS_PATH):
        return {}
    with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit("error: ~/.claude/settings.json is not a JSON object")
    return data


def atomic_write(data):
    d = os.path.dirname(SETTINGS_PATH) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, SETTINGS_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup():
    # A fresh install has no prior settings file to preserve; only back up an
    # existing one that we are about to modify.
    if not os.path.isfile(SETTINGS_PATH):
        return
    backup_path = "{}.bak-busybar-{}".format(
        SETTINGS_PATH, int(__import__("time").time())
    )
    import shutil

    shutil.copyfile(SETTINGS_PATH, backup_path)
    print("backup -> {}".format(backup_path))


def main():
    ap = argparse.ArgumentParser(description="Manage busybar limits Claude hooks")
    ap.add_argument(
        "--remove", action="store_true", help="remove the busybar hook groups instead"
    )
    args = ap.parse_args()

    data = load_settings()
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise SystemExit("error: ~/.claude/settings.json 'hooks' is not an object")

    modified = False
    if args.remove:
        for ev in EVENTS:
            entries = hooks.get(ev)
            if entries is None:
                print("{}: skipped".format(ev))
                continue
            if not isinstance(entries, list):
                print("{}: skipped".format(ev))
                continue
            before = len(entries)
            kept = [g for g in entries if not _group_has_command(g)]
            if len(kept) == before:
                print("{}: skipped".format(ev))
                continue
            if kept:
                hooks[ev] = kept
            else:
                hooks.pop(ev, None)
            modified = True
            print("{}: removed".format(ev))
        # Drop empty hooks sections only if nothing remains.
        if not hooks:
            data.pop("hooks", None)
    else:
        for ev in EVENTS:
            entries = hooks.get(ev)
            if entries is not None and not isinstance(entries, list):
                # Malformed; replace with a fresh list (preserving nothing of it).
                entries = None
            if _event_has_command(entries):
                print("{}: skipped".format(ev))
                continue
            group = {"hooks": [{"type": "command", "command": COMMAND}]}
            if entries is None:
                hooks[ev] = [group]
            else:
                hooks[ev] = entries + [group]
            modified = True
            print("{}: added".format(ev))

    if not modified:
        print("no changes")
        return 0

    backup()
    atomic_write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
