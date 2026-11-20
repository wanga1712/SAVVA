"""Quota-aware model selection at SAVVA model-operation boundaries."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any

from .models import ModelAdapter, ModelAdapterError, ModelResponse
from .usage import BudgetExceeded


class FailoverState(StrEnum):
    PRIMARY_ACTIVE = "PRIMARY_ACTIVE"
    PRIMARY_LIMITED = "PRIMARY_LIMITED"
    FALLBACK_ACTIVE = "FALLBACK_ACTIVE"
    PRIMARY_PROBING = "PRIMARY_PROBING"
    PRIMARY_RECOVERED = "PRIMARY_RECOVERED"
    BLOCKED = "BLOCKED"


class QuotaAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    EXHAUSTED = "EXHAUSTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    availability: QuotaAvailability
    next_check_at: float | None
    reached_type: str | None
    windows: tuple[dict[str, Any], ...]
    confidence: str = "unknown"

    @property
    def exhausted(self) -> bool:
        return self.availability == QuotaAvailability.EXHAUSTED

    @classmethod
    def parse(cls, document: dict[str, Any] | None) -> "QuotaSnapshot":
        if not isinstance(document, dict):
            return cls(QuotaAvailability.UNKNOWN, None, None, (), "unknown")
        ordinary_present = "ordinaryUsageAllowed" in document or "ordinary_usage_allowed" in document
        ordinary_allowed = document.get("ordinaryUsageAllowed", document.get("ordinary_usage_allowed"))
        root = document.get("rateLimits") if isinstance(document.get("rateLimits"), dict) else document
        buckets = document.get("rateLimitsByLimitId") or root.get("rateLimitsByLimitId")
        candidates = list(buckets.values()) if isinstance(buckets, dict) else [root]
        if isinstance(document.get("rateLimits"), dict) and document["rateLimits"] not in candidates:
            candidates.append(document["rateLimits"])
        windows: list[dict[str, Any]] = []
        resets: list[float] = []
        reached: str | None = None
        exhausted = False
        incomplete = not candidates
        for bucket in candidates:
            if not isinstance(bucket, dict):
                incomplete = True
                continue
            bucket_has_window = False
            value = bucket.get("rateLimitReachedType")
            known_reached_types = {
                "rate_limit_reached", "workspace_owner_credits_depleted",
                "workspace_member_credits_depleted", "workspace_owner_usage_limit_reached",
                "workspace_member_usage_limit_reached",
            }
            if value:
                if str(value).lower() in known_reached_types:
                    reached = str(value)
                    exhausted = True
                else:
                    incomplete = True
            if bucket.get("spendControlReached") is True:
                reached = reached or "spend_control_reached"
                exhausted = True
            elif "spendControlReached" in bucket and not isinstance(bucket.get("spendControlReached"), bool):
                incomplete = True
            for key in ("primary", "secondary"):
                window = bucket.get(key)
                if window is None and key in bucket:
                    continue
                if not isinstance(window, dict):
                    continue
                bucket_has_window = True
                if "usedPercent" not in window:
                    incomplete = True
                    continue
                windows.append({"limit_id": bucket.get("limitId"), "window": key,
                                "used_percent": window.get("usedPercent"),
                                "resets_at": window.get("resetsAt"),
                                "window_duration_mins": window.get("windowDurationMins")})
                try:
                    used_percent = float(window["usedPercent"])
                    if not math.isfinite(used_percent):
                        incomplete = True
                        continue
                    if used_percent >= 100:
                        exhausted = True
                        reset = window.get("resetsAt")
                        if reset is not None and float(reset) > time.time():
                            resets.append(float(reset))
                except (TypeError, ValueError):
                    incomplete = True
                    continue
            if not bucket_has_window and not reached and bucket.get("spendControlReached") is not True:
                incomplete = True
        if ordinary_allowed is True:
            availability, confidence = QuotaAvailability.AVAILABLE, "server_confirmed"
        elif exhausted:
            availability, confidence = QuotaAvailability.EXHAUSTED, "quota_confirmed"
        elif ordinary_present:
            # False without a confirmed quota reason and explicit null are not recovery.
            availability, confidence = QuotaAvailability.UNKNOWN, "server_unavailable"
        elif incomplete:
            availability, confidence = QuotaAvailability.UNKNOWN, "unknown"
        else:
            availability, confidence = QuotaAvailability.AVAILABLE, "legacy_limited"
        # All exhausted buckets must reset before probing the primary again.
        return cls(availability, max(resets) if resets else None, reached, tuple(windows), confidence)


class FailoverEngine(ModelAdapter):
    """Keep provider failures distinct; switch only on quota-confirmed primary exhaustion."""

    def __init__(self, primary: ModelAdapter, fallback: ModelAdapter, *,
                 session_cost_usd: float = 0.0, task_budget_usd: float = 1.0,
                 daily_cost_usd: float = 0.0, daily_budget_usd: float = 3.0,
                 max_request_cost_usd: float = 0.05, retry_backoff_seconds: int = 60,
                 state: str = FailoverState.PRIMARY_ACTIVE,
                 fallback_reason: str | None = None,
                 next_quota_check: float | None = None):
        self.primary, self.fallback = primary, fallback
        self.provider, self.model = primary.provider, primary.model
        self.state = FailoverState(state)
        self.fallback_reason = fallback_reason
        self.next_quota_check = next_quota_check
        if self.state == FailoverState.FALLBACK_ACTIVE:
            self.provider, self.model = fallback.provider, fallback.model
        self.session_cost_usd, self.task_budget_usd = session_cost_usd, task_budget_usd
        self.daily_cost_usd, self.daily_budget_usd = daily_cost_usd, daily_budget_usd
        self.max_request_cost_usd = max_request_cost_usd
        self.retry_backoff_seconds = max(30, retry_backoff_seconds)

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
                 max_tokens: int, timeout_seconds: float,
                 tool_choice: str | None = "auto") -> ModelResponse:
        if self.state == FailoverState.BLOCKED:
            raise BudgetExceeded("FAILOVER_BUDGET_BLOCKED", "Fallback budget is blocked; no additional provider calls are permitted.")
        recovered = False
        if self.state in {FailoverState.FALLBACK_ACTIVE, FailoverState.PRIMARY_LIMITED}:
            if self.next_quota_check and time.time() < self.next_quota_check:
                return self._fallback(messages, tools, max_tokens, timeout_seconds, tool_choice)
            self.state = FailoverState.PRIMARY_PROBING
            snapshot = self._read_quota()
            if snapshot.exhausted:
                self._activate_fallback(snapshot, "confirmed_quota_exhaustion")
                return self._fallback(messages, tools, max_tokens, timeout_seconds, tool_choice)
            if snapshot.availability == QuotaAvailability.UNKNOWN:
                self.state = FailoverState.FALLBACK_ACTIVE
                self.next_quota_check = time.time() + self.retry_backoff_seconds
                self.provider, self.model = self.fallback.provider, self.fallback.model
                return self._fallback(messages, tools, max_tokens, timeout_seconds, tool_choice)
            self.state = FailoverState.PRIMARY_RECOVERED
            recovered = True
            self.provider, self.model = self.primary.provider, self.primary.model
        else:
            snapshot = self._read_quota()
            if snapshot.exhausted:
                self._activate_fallback(snapshot, "confirmed_quota_exhaustion")
                return self._fallback(messages, tools, max_tokens, timeout_seconds, tool_choice)
        try:
            result = self.primary.complete(messages, tools, max_tokens=max_tokens,
                                           timeout_seconds=timeout_seconds,
                                           tool_choice=tool_choice)
        except ModelAdapterError as exc:
            snapshot = self._quota_from_error(exc)
            if exc.failure_kind == "quota" and snapshot.exhausted:
                self._activate_fallback(snapshot, exc.code)
                return self._fallback(messages, tools, max_tokens, timeout_seconds, tool_choice)
            if self.primary.provider == "codex_chatgpt" and exc.failure_kind in {"auth", "transient"}:
                # A confirmed primary auth failure or disconnected transport can
                # use the separately budgeted fallback. Do not persist BLOCKED:
                # a repaired credential/network must be probed on a later turn.
                reason = "primary_auth_failed" if exc.failure_kind == "auth" else "primary_disconnected"
                self._activate_fallback(snapshot, reason)
                return self._fallback(messages, tools, max_tokens, timeout_seconds, tool_choice)
            raise
        self.state = FailoverState.PRIMARY_RECOVERED if recovered else FailoverState.PRIMARY_ACTIVE
        self.provider, self.model = self.primary.provider, self.primary.model
        return result

    def _fallback(self, messages, tools, max_tokens, timeout_seconds, tool_choice):
        estimator = getattr(self.fallback, "max_cost_estimate", None)
        try:
            if estimator is None:
                raise ModelAdapterError("FALLBACK_PRICE_UNVERIFIED", "Fallback price could not be verified.")
            estimate = float(estimator(messages, tools, max_tokens))
        except ModelAdapterError:
            self.state = FailoverState.BLOCKED
            raise BudgetExceeded("FALLBACK_PRICE_UNVERIFIED", "Fallback price could not be verified; no paid request was sent.") from None
        if not math.isfinite(estimate) or estimate < 0:
            self.state = FailoverState.BLOCKED
            raise BudgetExceeded("FALLBACK_PRICE_UNVERIFIED", "Fallback price estimate is invalid; no paid request was sent.")
        if (estimate > self.max_request_cost_usd
                or self.session_cost_usd + estimate > self.task_budget_usd
                or self.daily_cost_usd + estimate > self.daily_budget_usd):
            self.state = FailoverState.BLOCKED
            raise BudgetExceeded("FAILOVER_BUDGET_EXHAUSTED", "Fallback API budget is exhausted.")
        # Reserve the estimate before issuing the paid request. If the request
        # times out, its final bill is unknown, so keep the reservation charged.
        self.session_cost_usd += estimate
        self.daily_cost_usd += estimate
        self.provider, self.model = self.fallback.provider, self.fallback.model
        self.state = FailoverState.FALLBACK_ACTIVE
        try:
            result = self.fallback.complete(messages, tools, max_tokens=max_tokens,
                                            timeout_seconds=timeout_seconds,
                                            tool_choice=tool_choice)
        except Exception as exc:
            exc.cost_usd = estimate
            exc.cost_verified = False
            exc.cost_estimated = True
            exc.provider = self.fallback.provider
            exc.model = self.fallback.model
            raise
        actual = result.usage.cost_usd
        reconciled = (getattr(result.usage, "cost_verified", False)
                      or getattr(result.usage, "cost_estimated", False))
        if not math.isfinite(actual) or actual < 0:
            reconciled = False
            result.usage.cost_verified = False
            result.usage.cost_estimated = False
        if reconciled:
            self.session_cost_usd += actual - estimate
            self.daily_cost_usd += actual - estimate
        else:
            actual = estimate
            result.usage.cost_usd = estimate
            result.usage.cost_estimated = True
        if (actual > self.max_request_cost_usd
                or self.session_cost_usd > self.task_budget_usd
                or self.daily_cost_usd > self.daily_budget_usd):
            self.state = FailoverState.BLOCKED
            raise BudgetExceeded("FAILOVER_BUDGET_EXHAUSTED", "Provider-reported cost exceeded the reserved fallback budget.",
                                 cost_usd=actual if (result.usage.cost_verified or result.usage.cost_estimated) else estimate,
                                 cost_verified=result.usage.cost_verified,
                                 cost_estimated=result.usage.cost_estimated,
                                 provider=self.fallback.provider, model=self.fallback.model)
        return result

    def _read_quota(self) -> QuotaSnapshot:
        reader = getattr(self.primary, "read_rate_limits", None)
        if reader is None:
            return QuotaSnapshot(QuotaAvailability.UNKNOWN, None, None, ())
        try:
            return QuotaSnapshot.parse(reader())
        except Exception:
            return QuotaSnapshot(QuotaAvailability.UNKNOWN, None, None, ())

    def _quota_from_error(self, error: ModelAdapterError) -> QuotaSnapshot:
        snapshot = QuotaSnapshot.parse(getattr(error, "quota_snapshot", None))
        if snapshot.exhausted:
            return snapshot
        return self._read_quota()

    def _activate_fallback(self, snapshot: QuotaSnapshot, reason: str) -> None:
        self.state = FailoverState.PRIMARY_LIMITED
        self.fallback_reason = reason
        self.next_quota_check = snapshot.next_check_at or time.time() + self.retry_backoff_seconds
        self.state = FailoverState.FALLBACK_ACTIVE
        self.provider, self.model = self.fallback.provider, self.fallback.model

    def status(self) -> dict[str, Any]:
        return {"state": self.state.value, "active_provider": self.provider,
                "active_model": self.model, "fallback_reason": self.fallback_reason,
                "next_quota_check": self.next_quota_check,
                "task_cost_usd": round(self.session_cost_usd, 8),
                "daily_cost_usd": round(self.daily_cost_usd, 8),
                "budget_mode": "conservative_preflight_with_reservation_and_reconciliation",
                "budget_limit_guaranteed": False}

    def close(self) -> None:
        close = getattr(self.primary, "close", None)
        if close:
            close()
