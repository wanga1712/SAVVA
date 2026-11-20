"""Multi-step model/tool orchestration with limits and outbound privacy."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.registry import ToolExecutionError, ToolRegistry
from ..core.schemas import ToolResult, validate_parameters
from .models import ModelAdapter, ModelAdapterError, ModelResponse, ToolCall
from .projects import ProjectService
from .context import ContextBudgetManager
from .prompts import get_prompt
from .sanitizer import sanitize_for_model
from .usage import BudgetExceeded, RunBudget, UsageLogger

SYSTEM_PROMPT = get_prompt("development").text


class AgentRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(slots=True)
class AgentRunResult:
    answer: str
    messages: list[dict[str, Any]]
    events: list[dict[str, Any]]
    usage: dict[str, Any]


class AgentOrchestrator:
    def __init__(
        self,
        *,
        adapter: ModelAdapter,
        registry: ToolRegistry,
        workspace_root: str | Path,
        max_tokens: int,
        max_tool_steps: int,
        max_requests_per_session: int,
        max_request_cost_usd: float,
        max_session_cost_usd: float,
        max_duration_seconds: float,
        usage_logger: UsageLogger,
        project_service: ProjectService | None = None,
    ):
        self.adapter = adapter
        self.registry = registry
        self.workspace_root = str(workspace_root)
        self.max_tokens = max_tokens
        self.max_tool_steps = max_tool_steps
        self.max_requests_per_session = max_requests_per_session
        self.max_request_cost_usd = max_request_cost_usd
        self.max_session_cost_usd = max_session_cost_usd
        self.max_duration_seconds = max_duration_seconds
        self.usage_logger = usage_logger
        self.project_service = project_service
        self.context_manager = ContextBudgetManager()
        self._tool_aliases, self._alias_to_tool = self._build_aliases()

    def run(
        self,
        messages: list[dict[str, Any]],
        user_message: str,
        *,
        session_id: str,
        initial_requests: int = 0,
        initial_cost_usd: float = 0.0,
    ) -> AgentRunResult:
        working = [message for message in messages if message.get("role") != "system"]
        working.append({"role": "user", "content": user_message})
        initial_plan = self.context_manager.build(
            working, user_message, self._model_tools(), self.adapter.provider,
            self.adapter.model, self.max_tokens,
        )
        if initial_plan.intent == "UNSUPPORTED_OTHER":
            answer = "Обработка этого типа материалов пока не подключена."
            working.append({"role": "assistant", "content": answer})
            return AgentRunResult(answer, working, [], {"requests": 0, "cost_usd": 0.0})
        deadline = time.monotonic() + self.max_duration_seconds
        budget = RunBudget(
            max_requests=self.max_requests_per_session,
            max_request_cost_usd=self.max_request_cost_usd,
            max_session_cost_usd=self.max_session_cost_usd,
            deadline=deadline,
            initial_requests=initial_requests,
            initial_cost_usd=initial_cost_usd,
        )
        events: list[dict[str, Any]] = []
        tool_names: list[str] = []

        try:
            for step_index in range(self.max_tool_steps):
                plan = self.context_manager.build(
                    working, user_message, self._model_tools(), self.adapter.provider,
                    self.adapter.model, self.max_tokens,
                )
                response = self._call_model(
                    plan.messages,
                    plan.tools,
                    budget=budget,
                    session_id=session_id,
                    tool_names=tool_names,
                    timeout_seconds=max(0.1, deadline - time.monotonic()),
                    plan=plan,
                )
                events.extend(self._model_events(response, step_index))
                working.append(response.to_assistant_message())
                if not response.tool_calls:
                    answer = response.content.strip()
                    answer = _with_verified_summary(answer, events)
                    events.append({"type": "assistant", "content": answer})
                    return AgentRunResult(answer, working, events, budget.usage_dict())

                allowed_tools = {
                    name
                    for tool in plan.tools
                    if isinstance(tool.get("function"), dict)
                    for name in (
                        tool["function"]["name"],
                        self._alias_to_tool.get(
                            tool["function"]["name"], tool["function"]["name"]
                        ),
                    )
                }
                for call in response.tool_calls:
                    budget.ensure_time()
                    tool_names.append(call.name)
                    result, event = self._execute_tool_call(
                        call, session_id, allowed_tool_names=allowed_tools
                    )
                    events.append(event)
                    if event.get("proposal"):
                        events.append(
                            {
                                "type": "proposal_ready",
                                "call_id": call.id,
                                "proposal": event["proposal"],
                            }
                        )
                    working.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": json.dumps(
                                sanitize_for_model(
                                    result.to_dict(), self.workspace_root
                                ),
                                ensure_ascii=False,
                                allow_nan=False,
                            ),
                        }
                    )

            final_plan = self.context_manager.build(
                working, user_message, self._model_tools(), self.adapter.provider,
                self.adapter.model, self.max_tokens,
            )
            final_messages = list(final_plan.messages)
            final_messages.append({
                "role": "system",
                "content": "Tool-step limit reached. Do not request more tools. Summarize verified results only.",
            })
            final_response = self._call_model(
                final_messages,
                [],
                budget=budget,
                session_id=session_id,
                tool_names=tool_names,
                timeout_seconds=max(0.1, deadline - time.monotonic()),
                plan=final_plan,
            )
            events.extend(self._model_events(final_response, self.max_tool_steps))
            working.append(final_response.to_assistant_message())
            answer = final_response.content or "Tool-step limit reached without a final answer."
            answer = _with_verified_summary(answer, events)
            events.append({"type": "assistant", "content": answer})
            return AgentRunResult(answer, working, events, budget.usage_dict())
        except (BudgetExceeded, ModelAdapterError) as exc:
            raise AgentRuntimeError(exc.code, exc.safe_message) from None

    def _call_model(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        budget: RunBudget,
        session_id: str,
        tool_names: list[str],
        timeout_seconds: float,
        plan: Any | None = None,
    ) -> ModelResponse:
        budget.before_request()
        safe_messages = sanitize_for_model(messages, self.workspace_root)
        started = time.perf_counter()
        try:
            response = self.adapter.complete(
                safe_messages,
                tools,
                max_tokens=plan.max_output_tokens if plan else self.max_tokens,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            self.usage_logger.record(
                session_id=session_id,
                provider=self.adapter.provider,
                model=self.adapter.model,
                duration_ms=round((time.perf_counter() - started) * 1000),
                status="error",
                tool_names=tool_names,
                intent=plan.intent if plan else "UNKNOWN",
                prompt_version=plan.prompt.version if plan else "unknown",
                system_tokens_estimated=plan.estimates.get("system", 0) if plan else 0,
                history_tokens_estimated=plan.estimates.get("history", 0) if plan else 0,
                tool_schema_tokens_estimated=plan.estimates.get("tool_schema", 0) if plan else 0,
                memory_tokens_estimated=0,
                document_tokens_estimated=0,
                output_tokens=0,
                cost_verified=False,
            )
            raise
        self.usage_logger.record(
            session_id=session_id,
            provider=self.adapter.provider,
            model=self.adapter.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            cost_usd=response.usage.cost_usd,
            duration_ms=round((time.perf_counter() - started) * 1000),
            status="success",
            tool_names=[call.name for call in response.tool_calls],
            intent=plan.intent if plan else "UNKNOWN",
            prompt_version=plan.prompt.version if plan else "unknown",
            system_tokens_estimated=plan.estimates.get("system", 0) if plan else 0,
            history_tokens_estimated=plan.estimates.get("history", 0) if plan else 0,
            tool_schema_tokens_estimated=plan.estimates.get("tool_schema", 0) if plan else 0,
            memory_tokens_estimated=0,
            document_tokens_estimated=0,
            tool_result_tokens_estimated=plan.estimates.get("tool_result", 0) if plan else 0,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            cost_verified=response.usage.cost_verified,
            tool_calls=len(response.tool_calls),
        )
        budget.record_cost(response.usage.cost_usd)
        return response

    def _execute_tool_call(
        self,
        call: ToolCall,
        session_id: str,
        *,
        allowed_tool_names: set[str] | None = None,
    ) -> tuple[ToolResult, dict[str, Any]]:
        registered_name = self._alias_to_tool.get(call.name, call.name)
        call_event = {
            "type": "tool_call",
            "call_id": call.id,
            "name": registered_name,
            "arguments": sanitize_for_model(call.arguments, self.workspace_root),
        }
        if allowed_tool_names is not None and call.name not in allowed_tool_names:
            result = ToolResult.failure(
                registered_name,
                "denied",
                "TOOL_NOT_ALLOWED_FOR_INTENT",
                "This tool was not enabled for the current request type.",
                0,
            )
            return result, {**call_event, "result": result.to_dict()}
        if call.parse_error:
            result = ToolResult.failure(
                registered_name,
                "error",
                "INVALID_ARGUMENTS",
                call.parse_error,
                0,
            )
            return result, {
                **call_event,
                "result": result.to_dict(),
            }

        try:
            contract = self.registry.get_contract(registered_name)
        except KeyError:
            result = ToolResult.failure(
                registered_name,
                "error",
                "TOOL_NOT_FOUND",
                "The model requested an unregistered tool.",
                0,
            )
            return result, {**call_event, "result": result.to_dict()}

        validation_errors = validate_parameters(
            call.arguments, contract["parameters"]
        )
        if validation_errors:
            result = ToolResult.failure(
                registered_name,
                "error",
                "INVALID_ARGUMENTS",
                "; ".join(validation_errors),
                0,
            )
            return result, {**call_event, "result": result.to_dict()}

        if registered_name == "files.apply" and self.project_service:
            proposal_id = str(call.arguments.get("proposal_id", ""))
            try:
                proposal = self.project_service.get_public(proposal_id, session_id)
            except ToolExecutionError as exc:
                result = ToolResult.failure(
                    registered_name,
                    "error",
                    exc.code,
                    exc.safe_message,
                    0,
                )
                return result, {**call_event, "result": result.to_dict()}
            result = ToolResult.failure(
                registered_name,
                "denied",
                "CONFIRMATION_REQUIRED",
                "The user must approve this exact proposal in the local UI.",
                0,
            )
            return result, {
                **call_event,
                "type": "confirmation_required",
                "result": result.to_dict(),
                "proposal": proposal,
            }

        if contract["risk_level"] != "read_only":
            result = ToolResult.failure(
                registered_name,
                "denied",
                "CONFIRMATION_REQUIRED",
                "A future write-capable tool requires explicit user confirmation.",
                0,
            )
            return result, {
                **call_event,
                "result": result.to_dict(),
                "confirmation_required": True,
            }

        result = self.registry.invoke(registered_name, call.arguments)
        if (
            result.status == "success"
            and self.project_service
            and registered_name in {"project.create", "files.propose"}
            and isinstance(result.data, dict)
            and result.data.get("proposal_id")
        ):
            proposal_id = str(result.data["proposal_id"])
            self.project_service.bind_session(proposal_id, session_id)
            proposal = self.project_service.get_public(proposal_id, session_id)
            return result, {
                **call_event,
                "result": result.to_dict(),
                "proposal": proposal,
            }
        return result, {**call_event, "result": result.to_dict()}

    def _model_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": self._tool_aliases[contract["name"]],
                    "description": contract["description"],
                    "parameters": contract["parameters"],
                },
                "_registered_name": contract["name"],
                "_risk_level": contract.get("risk_level", "read_only"),
            }
            for contract in self.registry.list_tools()
        ]

    def _build_aliases(self) -> tuple[dict[str, str], dict[str, str]]:
        aliases: dict[str, str] = {}
        reverse: dict[str, str] = {}
        for contract in self.registry.list_tools():
            name = contract["name"]
            base = "savva_" + re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
            alias = base
            suffix = 2
            while alias in reverse:
                alias = f"{base}_{suffix}"
                suffix += 1
            aliases[name] = alias
            reverse[alias] = name
        return aliases, reverse

    @staticmethod
    def _model_events(
        response: ModelResponse, step_index: int
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "model_usage",
                "step": step_index,
                "request_id": response.request_id,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "cost_usd": response.usage.cost_usd,
                "finish_reason": response.finish_reason,
            }
        ]


def _with_verified_summary(answer: str, events: list[dict[str, Any]]) -> str:
    detected: dict[str, dict[str, Any]] = {}
    readiness: dict[str, Any] | None = None
    for event in events:
        result = event.get("result")
        if not isinstance(result, dict) or result.get("status") != "success":
            continue
        data = result.get("data")
        if not isinstance(data, dict):
            continue
        raw_tools = data.get("tools")
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if isinstance(item, dict) and item.get("name"):
                    detected[str(item["name"])] = item
        if data.get("name") and "installed" in data:
            detected[str(data["name"])] = data
        core_tools = data.get("core_tools")
        if isinstance(core_tools, dict):
            for name, item in core_tools.items():
                if isinstance(item, dict):
                    detected[str(name)] = item
        if isinstance(data.get("readiness"), dict):
            readiness = data["readiness"]

    lines = ["**Проверено инструментами SAVVA**"]
    for name in ("python", "node", "npm", "git", "vscode"):
        item = detected.get(name)
        if not item or not item.get("installed"):
            continue
        version = item.get("version") or "версия не определена"
        lines.append(f"- {name}: {version}")
    if readiness:
        lines.append(
            "- React + TypeScript readiness: "
            + str(readiness.get("status", "unknown")).upper()
        )
        missing_required = readiness.get("missing_required") or []
        missing_optional = readiness.get("missing_recommended") or []
        lines.append(
            "- Обязательные компоненты: "
            + ("все найдены" if not missing_required else ", ".join(missing_required))
        )
        if missing_optional:
            lines.append("- Необязательные не найдены: " + ", ".join(missing_optional))
    if len(lines) == 1:
        return answer
    return "\n".join(lines) + "\n\n" + answer
