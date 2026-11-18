"""Unified OpenAI-compatible adapters for OpenRouter and Ollama."""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from .sanitizer import redact_text


class ModelAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, failure_kind: str = "unknown",
                 quota_snapshot: dict[str, Any] | None = None,
                 status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.failure_kind = failure_kind
        self.quota_snapshot = quota_snapshot
        self.status_code = status_code


@dataclass(slots=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cost_verified: bool = False
    cost_estimated: bool = False
    token_counts_verified: bool = False


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    arguments_raw: str
    parse_error: str | None = None

    def to_message_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments_raw,
            },
        }


@dataclass(slots=True)
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
    usage: ModelUsage
    finish_reason: str | None
    request_id: str | None = None

    def to_assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [call.to_message_dict() for call in self.tool_calls]
        return message


class ModelAdapter(ABC):
    provider: str
    model: str

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int,
        timeout_seconds: float,
        tool_choice: str | None = "auto",
    ) -> ModelResponse:
        raise NotImplementedError


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        ...


class UrllibJsonTransport:
    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            document: dict[str, Any] = {}
            try:
                detail = exc.read(1000).decode("utf-8", errors="replace")
                parsed = json.loads(detail)
                if isinstance(parsed, dict):
                    document = parsed
            except OSError:
                pass
            except (UnicodeError, json.JSONDecodeError):
                pass
            safe_detail = redact_text(detail)[:500]
            message = f"Provider returned HTTP {exc.code}."
            if safe_detail:
                message += f" {safe_detail}"
            error_document = document.get("error", {})
            error_code = str(error_document.get("code", "")).lower() if isinstance(error_document, dict) else ""
            error_kind = str(error_document.get("type", "")).lower() if isinstance(error_document, dict) else ""
            if exc.code in {401, 403}:
                failure_kind = "auth"
            elif "quota" in error_code or "usage_limit" in error_code or "insufficient_quota" in error_code:
                failure_kind = "quota"
            elif exc.code == 503:
                failure_kind = "overload"
            elif exc.code == 429 or "rate_limit" in error_kind:
                failure_kind = "rate_limit"
            else:
                failure_kind = "unknown"
            raise ModelAdapterError("MODEL_HTTP_ERROR", message, failure_kind=failure_kind,
                                    status_code=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelAdapterError(
                "MODEL_CONNECTION_ERROR", "The model provider could not be reached."
            ) from None
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ModelAdapterError(
                "MODEL_PROTOCOL_ERROR", "The model provider returned invalid JSON."
            ) from None
        if not isinstance(document, dict):
            raise ModelAdapterError(
                "MODEL_PROTOCOL_ERROR", "The model provider returned an invalid response."
            )
        return document


class OpenAICompatibleAdapter(ModelAdapter):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
        transport: JsonTransport | None = None,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.reasoning_effort = reasoning_effort
        self._transport = transport or UrllibJsonTransport()
        self._last_price: tuple[float, float] | None = None

    def max_cost_estimate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                          max_tokens: int) -> float:
        if self.provider == "ollama":
            return 0.0
        if self.provider != "openrouter":
            raise ModelAdapterError("MODEL_PRICE_UNAVAILABLE", "Fallback pricing is not configured.", failure_kind="protocol")
        prompt_price, completion_price = _openrouter_model_price(self.base_url, self.model)
        if any(not math.isfinite(value) or value < 0 for value in (prompt_price, completion_price)):
            raise ModelAdapterError("MODEL_PRICE_INVALID", "Fallback pricing is invalid.", failure_kind="protocol")
        self._last_price = (prompt_price, completion_price)
        request_shape = {"messages": messages, "tools": tools, "max_tokens": max_tokens,
                         "temperature": 0.1, "tool_choice": "auto" if tools else None}
        prompt_bytes = len(json.dumps(request_shape, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8")) + 2048
        return (prompt_bytes * prompt_price + max_tokens * completion_price) * 2.0

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int,
        timeout_seconds: float,
        tool_choice: str | None = "auto",
    ) -> ModelResponse:
        if self.provider == "openrouter" and not self._api_key:
            raise ModelAdapterError(
                "API_KEY_MISSING",
                "OPENROUTER_API_KEY is not configured on the backend.",
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if self.provider == "openrouter" and self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        headers = {
            "Accept": "application/json",
            "User-Agent": "SAVVA/0.2",
            "X-Title": "SAVVA",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        document = self._transport.post_json(
            self._completion_url(),
            payload,
            headers,
            timeout_seconds,
        )
        result = self._parse_response(document)
        if (self.provider == "openrouter" and not result.usage.cost_verified
                and result.usage.token_counts_verified and self._last_price):
            prompt_price, completion_price = self._last_price
            result.usage.cost_usd = (
                result.usage.prompt_tokens * prompt_price
                + result.usage.completion_tokens * completion_price
            )
            result.usage.cost_estimated = True
        return result

    def _completion_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def _parse_response(self, document: dict[str, Any]) -> ModelResponse:
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelAdapterError(
                "MODEL_PROTOCOL_ERROR", "The model response contains no choices."
            )
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            raise ModelAdapterError(
                "MODEL_PROTOCOL_ERROR", "The model response contains no message."
            )
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ModelAdapterError(
                "MODEL_PROTOCOL_ERROR", "The model returned malformed tool calls."
            )
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            raw_arguments = function.get("arguments", "{}")
            if not isinstance(raw_arguments, str):
                raw_arguments = json.dumps(raw_arguments, ensure_ascii=False)
            arguments: dict[str, Any] = {}
            parse_error = None
            try:
                decoded = json.loads(raw_arguments)
                if not isinstance(decoded, dict):
                    raise ValueError("arguments must be an object")
                arguments = decoded
            except (ValueError, TypeError):
                parse_error = "Tool arguments are not a valid JSON object."
            calls.append(
                ToolCall(
                    id=str(raw_call.get("id") or f"call_{uuid.uuid4().hex}"),
                    name=name,
                    arguments=arguments,
                    arguments_raw=raw_arguments,
                    parse_error=parse_error,
                )
            )
        usage_document = document.get("usage")
        usage_document = usage_document if isinstance(usage_document, dict) else {}
        provider_cost = usage_document.get("cost")
        try:
            parsed_provider_cost = float(provider_cost) if provider_cost is not None else 0.0
        except (TypeError, ValueError):
            parsed_provider_cost = float("nan")
        cost_verified = provider_cost is not None and math.isfinite(parsed_provider_cost) and parsed_provider_cost >= 0
        if not cost_verified:
            parsed_provider_cost = 0.0
        usage = ModelUsage(
            prompt_tokens=int(usage_document.get("prompt_tokens") or 0),
            completion_tokens=int(usage_document.get("completion_tokens") or 0),
            total_tokens=int(usage_document.get("total_tokens") or 0),
            cost_usd=parsed_provider_cost,
            cost_verified=cost_verified,
            token_counts_verified=("prompt_tokens" in usage_document and
                                   "completion_tokens" in usage_document and
                                   int(usage_document.get("prompt_tokens") or 0) >= 0 and
                                   int(usage_document.get("completion_tokens") or 0) >= 0),
        )
        return ModelResponse(
            content=str(content or "").strip(),
            tool_calls=calls,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            request_id=str(document.get("id") or "") or None,
        )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        reasoning_effort: str | None = "low",
        transport: JsonTransport | None = None,
    ):
        super().__init__(
            provider="openrouter",
            model=model,
            base_url=base_url,
            api_key=api_key,
            reasoning_effort=reasoning_effort,
            transport=transport,
        )


class OllamaAdapter(OpenAICompatibleAdapter):
    """OpenAI-compatible local adapter. This WIP never starts Ollama."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        transport: JsonTransport | None = None,
    ):
        super().__init__(
            provider="ollama",
            model=model,
            base_url=base_url,
            api_key=None,
            transport=transport,
        )


_MODEL_PRICE_CACHE: dict[tuple[str, str], tuple[float, float, float]] = {}
_MODEL_PRICE_LOCK = threading.Lock()


def _openrouter_model_price(base_url: str, model: str) -> tuple[float, float]:
    key = (base_url.rstrip("/"), model)
    with _MODEL_PRICE_LOCK:
        cached = _MODEL_PRICE_CACHE.get(key)
        if cached and cached[2] > time.time():
            return cached[0], cached[1]
    request = urllib.request.Request(
        f"{key[0]}/models?q={quote(model)}",
        headers={"Accept": "application/json", "User-Agent": "SAVVA/0.4"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(20_000_001)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ModelAdapterError("MODEL_PRICE_UNAVAILABLE", "Fallback pricing could not be verified.", failure_kind="transient") from None
    if len(raw) > 20_000_000:
        raise ModelAdapterError("MODEL_PRICE_TOO_LARGE", "Fallback pricing response exceeded its size limit.", failure_kind="protocol")
    try:
        document = json.loads(raw.decode("utf-8"))
        entries = document.get("data", [])
        item = next(value for value in entries if value.get("id") == model)
        pricing = item["pricing"]
        prompt, completion = float(pricing["prompt"]), float(pricing["completion"])
        if not math.isfinite(prompt) or not math.isfinite(completion) or prompt < 0 or completion < 0:
            raise ValueError("negative model price")
    except (UnicodeError, ValueError, KeyError, TypeError, StopIteration):
        raise ModelAdapterError("MODEL_PRICE_INVALID", "Fallback model pricing is unavailable or invalid.", failure_kind="protocol") from None
    with _MODEL_PRICE_LOCK:
        _MODEL_PRICE_CACHE[key] = (prompt, completion, time.time() + 60)
    return prompt, completion
