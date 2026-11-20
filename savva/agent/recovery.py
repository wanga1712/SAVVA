"""Bounded provider-failure classification and safe recovery event journal."""

from __future__ import annotations

import json
import time
from collections import deque
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any


class FailureCategory(StrEnum):
    PRIMARY_QUOTA_EXHAUSTED = "PRIMARY_QUOTA_EXHAUSTED"
    PRIMARY_AUTH_FAILED = "PRIMARY_AUTH_FAILED"
    PRIMARY_DISCONNECTED = "PRIMARY_DISCONNECTED"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNREACHABLE = "PROVIDER_UNREACHABLE"
    PROVIDER_REGION_BLOCKED = "PROVIDER_REGION_BLOCKED"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TAILSCALE_ROUTE_INVALID = "TAILSCALE_ROUTE_INVALID"
    LOCAL_RUNTIME_MISSING = "LOCAL_RUNTIME_MISSING"
    LOCAL_MODEL_NOT_INSTALLED = "LOCAL_MODEL_NOT_INSTALLED"
    LOCAL_INSUFFICIENT_MEMORY = "LOCAL_INSUFFICIENT_MEMORY"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_INTERNAL_ERROR = "BACKEND_INTERNAL_ERROR"


class RecoveryController:
    """Classify safe metadata, cap retries, and append events without prompt data."""

    def __init__(self, journal_path: Path | None = None, *, max_attempts: int = 2,
                 cooldown_seconds: float = 30.0):
        self.journal_path = journal_path
        self.max_attempts = max(1, max_attempts)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self._attempts: dict[str, int] = {}
        self._last_attempt: dict[str, float] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=100)
        self._lock = Lock()
        if self.journal_path and self.journal_path.is_file():
            try:
                lines = self.journal_path.read_text(encoding="utf-8").splitlines()[-100:]
                for line in lines:
                    event = json.loads(line)
                    if isinstance(event, dict) and "category" in event:
                        self._recent.append(event)
            except (OSError, ValueError):
                pass

    @staticmethod
    def classify(provider: str, error: BaseException, *, tailscale_ready: bool | None = None) -> FailureCategory:
        code = str(getattr(error, "code", "")).upper()
        kind = str(getattr(error, "failure_kind", "")).lower()
        provider = provider.lower()
        if code.startswith("TAILSCALE_") or tailscale_ready is False:
            return FailureCategory.TAILSCALE_ROUTE_INVALID
        if "QUOTA" in code or kind == "quota":
            return FailureCategory.PRIMARY_QUOTA_EXHAUSTED if "codex" in provider else FailureCategory.PROVIDER_RATE_LIMITED
        if "RATE_LIMIT" in code or kind == "rate_limit":
            return FailureCategory.PROVIDER_RATE_LIMITED
        if "REGION" in code or "GEO" in code:
            return FailureCategory.PROVIDER_REGION_BLOCKED
        if kind == "auth" or "AUTH" in code or "LOGIN" in code:
            return FailureCategory.PRIMARY_AUTH_FAILED if "codex" in provider else FailureCategory.PROVIDER_AUTH_FAILED
        if "NOT_INSTALLED" in code or "NOT_FOUND" in code:
            return FailureCategory.MODEL_UNAVAILABLE if "codex" in provider else FailureCategory.LOCAL_RUNTIME_MISSING
        if "MODEL" in code and "UNAVAILABLE" in code:
            return FailureCategory.MODEL_UNAVAILABLE
        if "CONNECTION" in code or kind == "transient":
            return FailureCategory.PRIMARY_DISCONNECTED if "codex" in provider else FailureCategory.PROVIDER_UNREACHABLE
        if code == "BACKEND_UNAVAILABLE":
            return FailureCategory.BACKEND_UNAVAILABLE
        return FailureCategory.BACKEND_INTERNAL_ERROR

    def may_attempt(self, operation_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            count = self._attempts.get(operation_id, 0)
            last = self._last_attempt.get(operation_id, 0.0)
            return count < self.max_attempts and (count == 0 or now - last >= self.cooldown_seconds)

    def mark_attempt(self, operation_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            count = self._attempts.get(operation_id, 0)
            if count >= self.max_attempts:
                return False
            if count and now - self._last_attempt.get(operation_id, 0.0) < self.cooldown_seconds:
                return False
            self._attempts[operation_id] = count + 1
            self._last_attempt[operation_id] = now
        return True

    def record(self, *, operation_id: str, provider: str, model: str,
               error: BaseException, tailscale_ready: bool | None = None) -> dict[str, Any]:
        category = self.classify(provider, error, tailscale_ready=tailscale_ready)
        event = {"timestamp": time.time(), "operation_id": operation_id[:100],
                 "provider": provider[:80], "model": model[:120],
                 "category": category.value,
                 "error_code": str(getattr(error, "code", "UNKNOWN"))[:100]}
        with self._lock:
            self._recent.append(event)
        if self.journal_path:
            try:
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
                with self.journal_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return event

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)[-max(0, min(limit, 100)):]
