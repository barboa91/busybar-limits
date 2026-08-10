"""OAuth token lifecycle for the Claude usage endpoint.

Linux port: Claude Code stores its OAuth token as plain JSON at
``~/.claude/.credentials.json`` (mode 0600) rather than in a Keychain, so this
module reads/writes that file directly instead of shelling out to
``security``. The payload shape (``{"claudeAiOauth": {...}}``) is identical to
what the macOS version reads, so the refresh logic below is unchanged.
"""

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request

from claude_limits.config import Config

REFRESH_URL = "https://claude.ai/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CACHE_SECONDS = 30


class AuthError(Exception):
    pass


_cache = {"token": None, "at": 0.0}


def _read_credentials_file(cfg: Config) -> dict:
    path = cfg.credentials_path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload["claudeAiOauth"]
    except FileNotFoundError as exc:
        raise AuthError(f"credentials file not found: {path}") from exc
    except (OSError, json.JSONDecodeError, KeyError, UnicodeDecodeError) as exc:
        raise AuthError(f"credentials file read failed: {exc}") from exc


def _refresh(cfg: Config, oauth: dict) -> dict:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": oauth["refreshToken"],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        REFRESH_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise AuthError(f"refresh failed: {exc}")

    new = dict(oauth)
    new["accessToken"] = data.get("access_token", oauth["accessToken"])
    new["refreshToken"] = data.get("refresh_token", oauth["refreshToken"])
    expires_in = data.get("expires_in") or 36000
    new["expiresAt"] = int((time.time() + expires_in) * 1000)
    return new


def _write_credentials_file(cfg: Config, oauth: dict) -> None:
    """Rewrite the credentials file, preserving any sibling top-level keys
    and matching Claude Code's own 0600 permissions. Atomic via temp file +
    os.replace so a crash mid-write can't corrupt the file the CLI relies on.
    """
    path = cfg.credentials_path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        payload = {}
    payload["claudeAiOauth"] = oauth

    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".credentials.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise AuthError(f"write-back failed: {exc}") from exc


def get_access_token(cfg: Config) -> str:
    """Return a usable access token, refreshing if near expiry (30 s cache)."""
    now = time.time()
    cached = _cache["token"]
    if cached and now - _cache["at"] < CACHE_SECONDS:
        return cached

    oauth = _read_credentials_file(cfg)
    try:
        if oauth.get("expiresAt", 0) / 1000 - now > 300:
            token = oauth["accessToken"]
        else:
            refreshed = _refresh(cfg, oauth)
            _write_credentials_file(cfg, refreshed)
            token = refreshed["accessToken"]
    except (KeyError, TypeError) as exc:
        raise AuthError(f"invalid credentials entry: {exc}") from exc

    _cache["token"] = token
    _cache["at"] = now
    return token
