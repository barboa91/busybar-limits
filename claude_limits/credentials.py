"""OAuth token lifecycle for the Claude usage endpoint."""

import getpass
import json
import subprocess
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


def _read_keychain(cfg: Config) -> dict:
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", cfg.keychain_service, "-w"],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise AuthError(f"keychain read failed: {exc}") from exc
    if proc.returncode != 0:
        raise AuthError("keychain read failed")
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
        return payload["claudeAiOauth"]
    except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
        raise AuthError("keychain read failed")


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


def _write_keychain(cfg: Config, oauth: dict) -> None:
    payload = json.dumps({"claudeAiOauth": oauth})
    try:
        proc = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-s",
                cfg.keychain_service,
                "-a",
                getpass.getuser(),
                "-w",
                payload,
                "-U",
            ],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise AuthError(f"write-back failed: {exc}") from exc
    if proc.returncode != 0:
        raise AuthError("write-back failed")


def get_access_token(cfg: Config) -> str:
    """Return a usable access token, refreshing if near expiry (30 s cache)."""
    now = time.time()
    cached = _cache["token"]
    if cached and now - _cache["at"] < CACHE_SECONDS:
        return cached

    oauth = _read_keychain(cfg)
    try:
        if oauth.get("expiresAt", 0) / 1000 - now > 300:
            token = oauth["accessToken"]
        else:
            refreshed = _refresh(cfg, oauth)
            _write_keychain(cfg, refreshed)
            token = refreshed["accessToken"]
    except (KeyError, TypeError) as exc:
        raise AuthError(f"invalid keychain entry: {exc}") from exc

    _cache["token"] = token
    _cache["at"] = now
    return token
