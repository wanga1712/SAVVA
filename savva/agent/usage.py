"""Usage/cost persistence and per-session budget enforcement."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class UsageRecord:
    request_id: str
    session_id: str
    timestamp_unix: float
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    duration_ms: int
    status: str
    tool_names: list[str]


class UsageLogger:
    """Append metadata only; prompts, responses, keys, and tool data are excluded."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        *,
        session_id: str,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        status: str = "success",
        tool_names: list[str] | None = None,
    ) -> UsageRecord:
        usage = UsageRecord(
            request_id=uuid.uuid4().hex,
            session_id=session_id,
            timestamp_unix=time.time(),
            provider=provider,
            model=model,
            prompt_tokens=max(0, int(prompt_tokens or 0)),
            completion_tokens=max(0, int(completion_tokens or 0)),
            total_tokens=max(0, int(total_tokens or 0)),
            cost_usd=max(0.0, float(cost_usd or 0.0)),
            duration_ms=max(0, int(duration_ms)),
            status=status,
            tool_names=list(tool_names or []),
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(asdict(usage), ensure_ascii=False) + "\n")
        except OSError:
            pass
        return usage


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


class RunBudget:
    def __init__(
        self,
        *,
        max_requests: int,
        max_request_cost_usd: float,
        max_session_cost_usd: float,
        deadline: float,
        initial_requests: int = 0,
        initial_cost_usd: float = 0.0,
    ):
        self.max_requests = max_requests
        self.max_request_cost_usd = max_request_cost_usd
        self.max_session_cost_usd = max_session_cost_usd
        self.deadline = deadline
        self.requests = max(0, initial_requests)
        self.cost_usd = max(0.0, initial_cost_usd)

    def before_request(self) -> None:
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("RUN_TIMEOUT", "The agent run exceeded its time limit.")
        if self.requests >= self.max_requests:
            raise BudgetExceeded(
                "REQUEST_LIMIT", "The per-session model request limit was reached."
            )
        self.requests += 1

    def ensure_time(self) -> None:
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("RUN_TIMEOUT", "The agent run exceeded its time limit.")

    def record_cost(self, cost_usd: float) -> None:
        cost = max(0.0, float(cost_usd or 0.0))
        if cost > self.max_request_cost_usd:
            raise BudgetExceeded(
                "REQUEST_COST_LIMIT", "A model request exceeded its cost limit."
            )
        self.cost_usd += cost
        if self.cost_usd > self.max_session_cost_usd:
            raise BudgetExceeded(
                "SESSION_COST_LIMIT", "The session cost limit was reached."
            )

    def usage_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "cost_usd": round(self.cost_usd, 8),
            "max_requests": self.max_requests,
            "max_session_cost_usd": self.max_session_cost_usd,
        }
