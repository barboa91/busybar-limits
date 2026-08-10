"""Config loading for the BusyBar limits tracker."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATE_DIR = "~/.local/share/busybar-limits"
CONFIG_PATH = "~/.config/busybar-limits/config.json"

DEFAULT_POLL_SECONDS = 180
DEFAULT_PRIORITY = 20
DEFAULT_ELEMENT_TIMEOUT_SECONDS = 480
DEFAULT_USER_AGENT = "claude-code/2.1.220"
DEFAULT_CREDENTIALS_PATH = "~/.claude/.credentials.json"
DEFAULT_THRESHOLDS = {"high": 85}
DEFAULT_DEVICE_MODE_GATE_ENABLED = True
DEFAULT_DEVICE_MODE_WS_BACKOFF_MAX_SECONDS = 30
DEFAULT_CLAUDE_ACTIVITY_GATE_ENABLED = True
DEFAULT_CLAUDE_ACTIVITY_CHECK_SECONDS = 3


@dataclass
class Config:
    device_url: str
    api_token: str
    poll_seconds: int
    priority: int
    element_timeout_seconds: int
    user_agent: str
    credentials_path: str
    state_dir: str
    thresholds: dict
    device_mode_gate_enabled: bool
    device_mode_ws_backoff_max_seconds: int
    claude_activity_gate_enabled: bool
    claude_activity_check_seconds: int

    @classmethod
    def defaults(cls, device_url: str = "", api_token: str = "") -> "Config":
        return cls(
            device_url=device_url,
            api_token=api_token,
            poll_seconds=DEFAULT_POLL_SECONDS,
            priority=DEFAULT_PRIORITY,
            element_timeout_seconds=DEFAULT_ELEMENT_TIMEOUT_SECONDS,
            user_agent=DEFAULT_USER_AGENT,
            credentials_path=_expand_home(DEFAULT_CREDENTIALS_PATH),
            state_dir=_expand_home(DEFAULT_STATE_DIR),
            thresholds=dict(DEFAULT_THRESHOLDS),
            device_mode_gate_enabled=DEFAULT_DEVICE_MODE_GATE_ENABLED,
            device_mode_ws_backoff_max_seconds=DEFAULT_DEVICE_MODE_WS_BACKOFF_MAX_SECONDS,
            claude_activity_gate_enabled=DEFAULT_CLAUDE_ACTIVITY_GATE_ENABLED,
            claude_activity_check_seconds=DEFAULT_CLAUDE_ACTIVITY_CHECK_SECONDS,
        )


def _expand_home(path: str) -> str:
    return str(Path(path).expanduser()) if path else path


def _coerce_int(path, data, key, default) -> int:
    try:
        return int(data.get(key, default))
    except (TypeError, ValueError):
        raise SystemExit(f"config {path} key {key!r} must be an integer")


def _coerce_bool(path, data, key, default) -> bool:
    val = data.get(key, default)
    if isinstance(val, bool):
        return val
    raise SystemExit(f"config {path} key {key!r} must be a boolean")


def load() -> Config:
    """Read config JSON, raising SystemExit with a helpful message if missing."""
    path = os.environ.get("BUSYBAR_LIMITS_CONFIG", CONFIG_PATH)
    config_path = _expand_home(path)

    if not os.path.isfile(config_path):
        raise SystemExit(
            f"config file not found: {config_path}\n"
            f'copy config.example.json to "{config_path}" and set device_url/api_token.'
        )

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not read config {config_path}: {exc}")

    for key in ("device_url", "api_token"):
        if key not in data:
            raise SystemExit(f"config {config_path} is missing required key: {key}")

    cfg = Config.defaults(device_url=data["device_url"], api_token=data["api_token"])
    cfg.poll_seconds = _coerce_int(config_path, data, "poll_seconds", DEFAULT_POLL_SECONDS)
    cfg.priority = _coerce_int(config_path, data, "priority", DEFAULT_PRIORITY)
    cfg.element_timeout_seconds = _coerce_int(
        config_path, data, "element_timeout_seconds", DEFAULT_ELEMENT_TIMEOUT_SECONDS
    )
    cfg.user_agent = data.get("user_agent", cfg.user_agent)
    cfg.credentials_path = _expand_home(data.get("credentials_path", cfg.credentials_path))
    cfg.state_dir = _expand_home(data.get("state_dir", cfg.state_dir))
    cfg.thresholds = data.get("thresholds", cfg.thresholds)
    cfg.device_mode_gate_enabled = _coerce_bool(
        config_path, data, "device_mode_gate_enabled", DEFAULT_DEVICE_MODE_GATE_ENABLED
    )
    cfg.device_mode_ws_backoff_max_seconds = _coerce_int(
        config_path, data, "device_mode_ws_backoff_max_seconds",
        DEFAULT_DEVICE_MODE_WS_BACKOFF_MAX_SECONDS,
    )
    cfg.claude_activity_gate_enabled = _coerce_bool(
        config_path, data, "claude_activity_gate_enabled",
        DEFAULT_CLAUDE_ACTIVITY_GATE_ENABLED,
    )
    cfg.claude_activity_check_seconds = _coerce_int(
        config_path, data, "claude_activity_check_seconds",
        DEFAULT_CLAUDE_ACTIVITY_CHECK_SECONDS,
    )
    return cfg
