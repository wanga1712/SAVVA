"""Deterministic intent routing and bounded model-facing context."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from .prompts import PromptSpec, get_prompt

DEV = re.compile(r"\b(create|build|develop|implement|refactor|code|python|bug|application|project|fix|debug|\u0441\u043e\u0437\u0434\u0430\u0439|\u0438\u0441\u043f\u0440\u0430\u0432\u0438|\u043e\u0448\u0438\u0431|\u043a\u043e\u0434|\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c|\u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442|\u043c\u043e\u0434\u0443\u043b)\b", re.I)
ACTION = re.compile(r"\b(installed|computer|filesystem|files|software|environment|tools?|\u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d|\u043a\u043e\u043c\u043f\u044c\u044e\u0442\u0435\u0440|\u0444\u0430\u0439\u043b|\u0441\u0438\u0441\u0442\u0435\u043c\u0430|\u043e\u043a\u0440\u0443\u0436\u0435\u043d\u0438)\b", re.I)
OTHER = re.compile(r"\b(create|analy[sz]e|summari[sz]e|\u0441\u043e\u0437\u0434\u0430\u0439|\u043f\u0440\u043e\u0430\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u0443\u0439|\u0441\u0434\u0435\u043b\u0430\u0439)\b.{0,40}\b(pdf|document|presentation|spreadsheet|\u043f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446|\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442|\u0442\u0430\u0431\u043b\u0438\u0446)", re.I)


def estimate_tokens(value: Any) -> int:
    """Conservative rough estimate; never represented as provider usage."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


@dataclass(frozen=True, slots=True)
class ContextPlan:
    intent: str
    prompt: PromptSpec
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    max_output_tokens: int
    estimates: dict[str, int]


class IntentRouter:
    @staticmethod
    def route(text: str) -> str:
        if OTHER.search(text):
            return "UNSUPPORTED_OTHER"
        if DEV.search(text):
            return "DEVELOPMENT"
        if ACTION.search(text):
            return "ACTION"
        return "CHAT"


class ContextBudgetManager:
    SYSTEM_BUDGET = 900
    HISTORY_BUDGET = 1400
    TOOL_BUDGET = 2400
    DATA_BUDGET = 1800

    def build(
        self,
        history: list[dict[str, Any]],
        current_request: str,
        all_tools: list[dict[str, Any]],
        provider: str,
        model: str,
        configured_max_tokens: int,
    ) -> ContextPlan:
        intent = IntentRouter.route(current_request)
        if intent == "UNSUPPORTED_OTHER":
            return ContextPlan(intent, get_prompt("chat"), [], [], 0, {})
        prompt = get_prompt({"CHAT": "chat", "DEVELOPMENT": "development", "ACTION": "action"}[intent])
        selected = []
        if intent == "DEVELOPMENT":
            selected = [tool for tool in all_tools if _tool_name(tool) in {"development.inspect", "files.read", "files.propose", "files.diff", "project.create", "project.inspect", "project.validate", "software.detect", "system.summary"}]
        elif intent == "ACTION":
            broad_tool_request = bool(re.search(r"\b(tools?|\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u044b?)\b", current_request, re.I))
            selected = [tool for tool in all_tools if _tool_name(tool) in {"development.inspect", "files.read", "project.inspect", "software.detect", "system.summary"} or (broad_tool_request and tool.get("_risk_level") == "read_only")]
        selected = _fit_items(selected, self.TOOL_BUDGET)
        messages: list[dict[str, Any]] = [{"role": "system", "content": prompt.text + f"\nCurrent provider/model: {provider}/{model}. SAVVA has no separate personal-memory store."}]
        history_budget = self.HISTORY_BUDGET
        kept: list[dict[str, Any]] = []
        current_index = next((index for index in range(len(history) - 1, -1, -1)
                              if history[index].get("role") == "user"
                              and history[index].get("content") == current_request), len(history))
        prior_history = history[:current_index]
        operation_tail = history[current_index:] if current_index < len(history) else []
        for message in reversed(prior_history):
            role = message.get("role")
            if role not in {"user", "assistant", "tool"}:
                continue
            candidate = {key: value for key, value in message.items() if key in {"role", "content", "name", "tool_call_id", "tool_calls"}}
            candidate["content"] = str(candidate.get("content") or "")
            size = estimate_tokens(candidate)
            if size > history_budget:
                break
            kept.append(candidate)
            history_budget -= size
        messages.extend(reversed(kept))
        tool_result_tokens = 0
        tool_result_remaining = self.DATA_BUDGET
        for item in operation_tail:
            if item.get("role") not in {"user", "assistant", "tool"}:
                continue
            candidate = {key: value for key, value in item.items() if key in {"role", "content", "name", "tool_call_id", "tool_calls"}}
            if candidate.get("role") == "tool":
                content = str(candidate.get("content") or "")
                size = estimate_tokens(content)
                if size > tool_result_remaining:
                    if tool_result_remaining > 32:
                        candidate["content"] = _sentence_safe_excerpt(content, tool_result_remaining)
                        size = estimate_tokens(candidate["content"])
                    else:
                        candidate["content"] = "[tool result omitted: operation context budget reached]"
                        size = estimate_tokens(candidate["content"])
                tool_result_tokens += size
                tool_result_remaining = max(0, tool_result_remaining - size)
            messages.append(candidate)
        if not operation_tail:
            messages.append({"role": "user", "content": current_request})
        tool_tokens = estimate_tokens(selected) if selected else 0
        system_tokens = estimate_tokens(messages[0]["content"])
        history_tokens = sum(estimate_tokens(item) for item in messages[1:-1])
        request_complexity = estimate_tokens(current_request)
        if intent == "CHAT":
            output = min(configured_max_tokens, 160 if request_complexity < 100 else 384)
        elif intent == "ACTION":
            output = min(configured_max_tokens, 512)
        else:
            output = min(configured_max_tokens, 1024)
        return ContextPlan(intent, prompt, messages, selected, output, {
            "system": system_tokens, "history": history_tokens,
            "tool_schema": tool_tokens, "memory": 0, "document": 0,
            "tool_result": tool_result_tokens,
        })


def _tool_name(tool: dict[str, Any]) -> str:
    return str(tool.get("_registered_name") or "")


def _fit_items(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    kept = []
    used = 0
    for item in items:
        clean = {key: value for key, value in item.items() if key not in {"_registered_name", "_risk_level"}}
        size = estimate_tokens(clean)
        if used + size <= budget:
            kept.append(clean)
            used += size
    return kept


def _sentence_safe_excerpt(text: str, budget: int) -> str:
    """Keep complete sentences in the model copy and label omitted tool data."""
    max_bytes = max(1, budget * 3 - 80)
    prefix = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    stops = [prefix.rfind(mark) for mark in (". ", ".\n", "! ", "? ", "; ")]
    boundary = max(stops)
    if boundary > 0:
        prefix = prefix[:boundary + 1]
    return prefix.rstrip() + " [tool output shortened for context budget]"
