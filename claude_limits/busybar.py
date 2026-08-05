"""BUSY Bar device client.

Thin HTTP wrapper around the device's canvas API. ``draw`` pushes a frame of
elements (upserted by ``id`` for this application), ``clear`` removes all of
this application's elements. Only network-level failures raise ``DeviceError``;
HTTP error statuses are returned as ``int`` so callers can branch on
200/400/401/409.
"""

import json
import urllib.error
import urllib.request

from claude_limits.config import Config

DRAW_PATH = "/api/display/draw"
TIMEOUT = 10
APP_NAME = "claude_limits"


class DeviceError(Exception):
    """Raised only when the device is unreachable (network failure)."""


class BusyBarClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _build_request(self, elements: list) -> urllib.request.Request:
        """Build the draw POST request (headers + payload) for ``elements``."""
        url = f"{self.cfg.device_url}{DRAW_PATH}"
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_token:
            headers["X-API-Token"] = self.cfg.api_token
        body = json.dumps(
            {
                "application_name": APP_NAME,
                "priority": self.cfg.priority,
                "elements": elements,
            }
        ).encode("utf-8")
        return urllib.request.Request(
            url, data=body, headers=headers, method="POST"
        )

    def draw(self, elements: list) -> int:
        """POST a frame; returns the HTTP status int. Raises DeviceError on
        network failure."""
        return self._request(self._build_request(elements))

    def draw_capture(self, elements: list) -> tuple:
        """POST a frame and return ``(status int|None, body str)``.

        Unlike :meth:`draw`, errors are surfaced in the tuple rather than
        raised: HTTP error statuses become ``(int(code), body)`` and network
        failures ``(None, message)``.
        """
        req = self._build_request(elements)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return int(resp.status), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            return None, str(getattr(exc, "reason", exc))
        except (OSError, TimeoutError) as exc:
            return None, str(exc)

    def clear(self) -> int:
        """DELETE all of this application's elements; returns HTTP status."""
        url = f"{self.cfg.device_url}{DRAW_PATH}?application_name={APP_NAME}"
        headers = {}
        if self.cfg.api_token:
            headers["X-API-Token"] = self.cfg.api_token
        return self._request(
            urllib.request.Request(url, headers=headers, method="DELETE")
        )

    def _request(self, req: urllib.request.Request) -> int:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return int(resp.status)
        except urllib.error.URLError as exc:
            # URLError covers DNS failures, refused connections, timeouts, and
            # HTTP errors with no readable response body.
            if isinstance(exc, urllib.error.HTTPError):
                return int(exc.code)
            raise DeviceError(f"device unreachable: {exc}") from exc
        except TimeoutError as exc:
            raise DeviceError(f"device request timed out: {exc}") from exc
        except OSError as exc:
            raise DeviceError(f"device request failed: {exc}") from exc
