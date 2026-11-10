"""Universal registry and guarded executor for SAVVA tools."""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from .permissions import (
    IMPLEMENTED_PERMISSIONS,
    IMPLEMENTED_RISK_LEVELS,
    WRITE_RISK_LEVEL,
    Permission,
    PermissionPolicy,
)
from .schemas import ToolContract, ToolResult, validate_parameters

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRegistrationError(ValueError):
    """Raised when a tool contract violates registry safety rules."""


class ToolExecutionError(Exception):
    """Expected, safe-to-return tool failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


class ToolRegistry:
    """Registry that executes only explicitly registered read-only handlers."""

    def __init__(self, policy: PermissionPolicy | None = None):
        self._contracts: dict[str, ToolContract] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._policy = policy or PermissionPolicy()

    def register(self, contract: ToolContract, handler: ToolHandler) -> None:
        self._validate_contract(contract, handler)
        if contract.name in self._contracts:
            raise ToolRegistrationError(f"Tool already registered: {contract.name}")
        self._contracts[contract.name] = contract
        self._handlers[contract.name] = handler

    def list_tools(self) -> list[dict[str, Any]]:
        return [self._contracts[name].to_dict() for name in sorted(self._contracts)]

    def get_contract(self, name: str) -> dict[str, Any]:
        return self._contracts[name].to_dict()

    def get_parameter_schema(self, name: str) -> dict[str, Any]:
        return self._contracts[name].to_dict()["parameters"]

    def invoke(
        self,
        name: str,
        parameters: dict[str, Any] | None = None,
        policy: PermissionPolicy | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        contract = self._contracts.get(name)
        if contract is None:
            return self._failure(
                name,
                started,
                "error",
                "TOOL_NOT_FOUND",
                "The requested tool is not registered.",
            )

        active_policy = policy or self._policy
        missing = active_policy.missing(contract.permissions)
        if missing:
            return self._failure(
                name,
                started,
                "denied",
                "PERMISSION_DENIED",
                "Missing permission: " + ", ".join(missing),
            )

        args = {} if parameters is None else parameters
        validation_errors = validate_parameters(args, contract.parameters)
        if validation_errors:
            return self._failure(
                name,
                started,
                "error",
                "INVALID_ARGUMENTS",
                "; ".join(validation_errors),
            )

        outcome = _run_with_timeout(
            self._handlers[name],
            args,
            contract.timeout_seconds,
        )
        if outcome.kind == "timeout":
            return self._failure(
                name,
                started,
                "timeout",
                "TOOL_TIMEOUT",
                f"Tool exceeded its {contract.timeout_seconds:g}s timeout.",
            )
        if outcome.kind == "exception":
            if isinstance(outcome.payload, ToolExecutionError):
                return self._failure(
                    name,
                    started,
                    "error",
                    outcome.payload.code,
                    outcome.payload.safe_message,
                )
            return self._failure(
                name,
                started,
                "error",
                "HANDLER_ERROR",
                f"Tool handler failed ({type(outcome.payload).__name__}).",
            )

        data = outcome.payload
        if not isinstance(data, dict):
            return self._failure(
                name,
                started,
                "error",
                "INVALID_RESULT",
                "Tool handler must return a JSON object.",
            )
        try:
            normalized = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError):
            return self._failure(
                name,
                started,
                "error",
                "NON_JSON_RESULT",
                "Tool result is not JSON serializable.",
            )
        return ToolResult.success(name, normalized, self._duration(started))

    def _validate_contract(self, contract: ToolContract, handler: ToolHandler) -> None:
        if not callable(handler):
            raise ToolRegistrationError("Handler must be callable.")
        if not contract.name or "." not in contract.name:
            raise ToolRegistrationError("Tool name must use a namespace such as system.summary.")
        if contract.risk_level not in IMPLEMENTED_RISK_LEVELS:
            raise ToolRegistrationError("Unsupported tool risk level.")
        if contract.timeout_seconds <= 0:
            raise ToolRegistrationError("Timeout must be positive.")
        try:
            permissions = {Permission(item) for item in contract.permissions}
        except ValueError as exc:
            raise ToolRegistrationError("Contract contains an unknown permission.") from exc
        if not permissions:
            raise ToolRegistrationError("Every tool must declare a permission.")
        if not permissions <= IMPLEMENTED_PERMISSIONS:
            raise ToolRegistrationError("Contract requires an unimplemented permission.")
        if Permission.FILES_WRITE in permissions and contract.risk_level != WRITE_RISK_LEVEL:
            raise ToolRegistrationError("files.write tools must use the write risk level.")
        if Permission.FILES_WRITE not in permissions and contract.risk_level == WRITE_RISK_LEVEL:
            raise ToolRegistrationError("Write tools must declare files.write.")
        if not isinstance(contract.parameters, dict):
            raise ToolRegistrationError("Parameters must be a JSON Schema object.")

    @staticmethod
    def _duration(started: float) -> int:
        return max(0, round((time.perf_counter() - started) * 1000))

    @classmethod
    def _failure(
        cls,
        name: str,
        started: float,
        status: str,
        code: str,
        message: str,
    ) -> ToolResult:
        return ToolResult.failure(name, status, code, message, cls._duration(started))


class _Outcome:
    def __init__(self, kind: str, payload: Any):
        self.kind = kind
        self.payload = payload


def _run_with_timeout(handler: ToolHandler, args: dict[str, Any], timeout: float) -> _Outcome:
    output: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            output.put(("success", handler(args)))
        except BaseException as exc:  # The registry is the tool exception boundary.
            output.put(("exception", exc))

    worker = threading.Thread(target=run, name="savva-tool", daemon=True)
    worker.start()
    try:
        kind, payload = output.get(timeout=timeout)
    except queue.Empty:
        return _Outcome("timeout", None)
    return _Outcome(kind, payload)
