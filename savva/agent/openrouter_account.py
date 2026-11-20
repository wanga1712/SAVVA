"""Small, read-only OpenRouter account check for the saved inference key."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OpenRouterAccountError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


class OpenRouterAccountClient:
    def key_status(self, api_key: str) -> dict[str, Any]:
        if not api_key.strip():
            raise OpenRouterAccountError("API_KEY_MISSING", "OpenRouter API key is not configured.")
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/key",
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key.strip()}", "User-Agent": "SAVVA"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read(256_000).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            codes = {401: ("OPENROUTER_AUTH_FAILED", "OpenRouter rejected this API key."),
                     403: ("OPENROUTER_ACCESS_DENIED", "OpenRouter denied access to key status."),
                     429: ("OPENROUTER_RATE_LIMITED", "OpenRouter temporarily rate-limited the status check.")}
            code, message = codes.get(exc.code, ("OPENROUTER_HTTP_ERROR", f"OpenRouter returned HTTP {exc.code}."))
            raise OpenRouterAccountError(code, message) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OpenRouterAccountError("OPENROUTER_UNAVAILABLE", "OpenRouter could not be reached.") from None
        except (UnicodeError, json.JSONDecodeError):
            raise OpenRouterAccountError("OPENROUTER_INVALID_RESPONSE", "OpenRouter returned invalid key status.") from None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise OpenRouterAccountError("OPENROUTER_INVALID_RESPONSE", "OpenRouter returned incomplete key status.")
        return {
            "authenticated": True,
            "limit": _number(data.get("limit")),
            "limit_remaining": _number(data.get("limit_remaining")),
            "usage": _number(data.get("usage")),
            "usage_daily": _number(data.get("usage_daily")),
            "usage_weekly": _number(data.get("usage_weekly")),
            "usage_monthly": _number(data.get("usage_monthly")),
            "limit_reset": _string(data.get("limit_reset")),
            "is_management_key": data.get("is_management_key") is True,
        }


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    if result < 0 or result != result or result in {float("inf"), float("-inf")}:
        return None
    return int(result) if result.is_integer() else result


def _string(value: Any) -> str | None:
    return value[:120] if isinstance(value, str) else None
