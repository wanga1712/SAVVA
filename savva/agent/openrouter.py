"""Read-only OpenRouter account and model-catalog operations."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol


class JsonGetTransport(Protocol):
    def get_json(self, url: str, headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
        ...


class UrllibJsonGetTransport:
    def get_json(self, url: str, headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(8_000_000)
        except urllib.error.HTTPError as exc:
            codes = {401: "AUTH_FAILED", 403: "ACCESS_DENIED", 429: "RATE_LIMITED"}
            code = codes.get(exc.code, "OPENROUTER_HTTP_ERROR")
            messages = {
                "AUTH_FAILED": "OpenRouter rejected this API key.",
                "ACCESS_DENIED": "OpenRouter denied access to this resource.",
                "RATE_LIMITED": "OpenRouter temporarily rate-limited this request.",
                "OPENROUTER_HTTP_ERROR": f"OpenRouter returned HTTP {exc.code}.",
            }
            raise OpenRouterError(code, messages[code]) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OpenRouterError("OPENROUTER_UNAVAILABLE", "OpenRouter could not be reached.") from None
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise OpenRouterError("OPENROUTER_INVALID_RESPONSE", "OpenRouter returned invalid JSON.") from None
        if not isinstance(document, dict):
            raise OpenRouterError("OPENROUTER_INVALID_RESPONSE", "OpenRouter returned an invalid response.")
        return document


class OpenRouterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


class OpenRouterAccountClient:
    BASE_URL = "https://openrouter.ai/api/v1"
    CANDIDATE_IDS = (
        "qwen/qwen3-coder-next",
        "qwen/qwen3-coder-flash",
    )

    def __init__(self, transport: JsonGetTransport | None = None):
        self._transport = transport or UrllibJsonGetTransport()

    def key_status(self, api_key: str) -> dict[str, Any]:
        response = self._get("/key", api_key)
        data = response.get("data")
        if not isinstance(data, dict):
            raise OpenRouterError("OPENROUTER_INVALID_RESPONSE", "OpenRouter returned incomplete key status.")
        return {
            "limit": _safe_number(data.get("limit")),
            "limit_remaining": _safe_number(data.get("limit_remaining")),
            "limit_reset": _safe_string(data.get("limit_reset")),
            "usage": _safe_number(data.get("usage")),
            "usage_daily": _safe_number(data.get("usage_daily")),
            "usage_weekly": _safe_number(data.get("usage_weekly")),
            "usage_monthly": _safe_number(data.get("usage_monthly")),
            "is_management_key": data.get("is_management_key") is True,
        }

    def discover_models(self, api_key: str) -> dict[str, Any]:
        response = self._get("/models", api_key)
        rows = response.get("data")
        if not isinstance(rows, list):
            raise OpenRouterError("OPENROUTER_INVALID_RESPONSE", "OpenRouter returned no model catalog.")
        by_id = {row.get("id"): row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
        candidates = [self._model_record(model_id, by_id.get(model_id)) for model_id in self.CANDIDATE_IDS]
        text_models = [row for row in rows if isinstance(row, dict) and _supports_text(row)]
        priced_text_models = [row for row in text_models if _price_score(row) != float("inf")]
        cheapest = min(priced_text_models, key=_price_score, default=None)
        candidates.append(self._model_record("luna-low-cost-catalog-candidate", cheapest, catalog_candidate=True))
        image_models = [row for row in text_models if _supports_image(row) and _price_score(row) != float("inf")]
        cheapest_image = min(image_models, key=_price_score, default=None)
        candidates.append(self._model_record("ui-image-catalog-candidate", cheapest_image, catalog_candidate=True))
        return {"catalog_count": len(rows), "models": candidates}

    def _get(self, path: str, api_key: str) -> dict[str, Any]:
        if not api_key.strip():
            raise OpenRouterError("API_KEY_MISSING", "OpenRouter API key is not configured.")
        return self._transport.get_json(
            self.BASE_URL + path,
            {"Accept": "application/json", "Authorization": f"Bearer {api_key.strip()}", "User-Agent": "SAVVA/0.3"},
            15,
        )

    @staticmethod
    def _model_record(requested_id: str, row: dict[str, Any] | None, *, catalog_candidate: bool = False) -> dict[str, Any]:
        if not row:
            return {
                "provider": "openrouter", "model_id": requested_id,
                "context_length": None, "tool_calling": False,
                "structured_output": False, "image_input": False,
                "input_price": None, "output_price": None,
                "availability_status": "UNAVAILABLE", "catalog_only": True,
            }
        architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
        modalities = architecture.get("input_modalities") if isinstance(architecture.get("input_modalities"), list) else []
        pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
        params = row.get("supported_parameters") if isinstance(row.get("supported_parameters"), list) else []
        return {
            "provider": "openrouter",
            "model_id": row.get("id"),
            "name": _safe_string(row.get("name")),
            "context_length": _safe_number(row.get("context_length")),
            "tool_calling": any(item in params for item in ("tools", "tool_choice")),
            "structured_output": any(item in params for item in ("response_format", "structured_outputs")),
            "image_input": "image" in modalities,
            "input_price": _safe_number(pricing.get("prompt")),
            "output_price": _safe_number(pricing.get("completion")),
            "availability_status": "CATALOG_ONLY",
            "catalog_candidate": catalog_candidate,
        }


def _safe_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (float, int, str)):
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    if parsed < 0 or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _safe_string(value: Any) -> str | None:
    return value[:120] if isinstance(value, str) else None


def _supports_text(row: dict[str, Any]) -> bool:
    architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
    modalities = architecture.get("input_modalities") if isinstance(architecture.get("input_modalities"), list) else ["text"]
    return "text" in modalities


def _supports_image(row: dict[str, Any]) -> bool:
    architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
    modalities = architecture.get("input_modalities") if isinstance(architecture.get("input_modalities"), list) else []
    return "image" in modalities


def _price_score(row: dict[str, Any]) -> float:
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
    prompt = _safe_number(pricing.get("prompt"))
    completion = _safe_number(pricing.get("completion"))
    if prompt is None or completion is None:
        return float("inf")
    return float(prompt) + float(completion)
