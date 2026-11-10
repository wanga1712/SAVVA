from __future__ import annotations

import json
import time
import unittest

from savva.core.permissions import Permission, PermissionPolicy
from savva.core.registry import (
    ToolRegistrationError,
    ToolRegistry,
)
from savva.core.schemas import ToolContract


def contract(
    name: str = "demo.echo",
    *,
    risk_level: str = "read_only",
    permissions: tuple[str, ...] = ("system.read",),
    timeout: float = 1,
) -> ToolContract:
    return ToolContract(
        name=name,
        description="Echo test data",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        permissions=permissions,
        risk_level=risk_level,
        timeout_seconds=timeout,
    )


class ToolRegistryTests(unittest.TestCase):
    def test_register_and_invoke_new_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(contract(), lambda args: {"echo": args["value"]})

        result = registry.invoke("demo.echo", {"value": "hello"})

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data, {"echo": "hello"})
        self.assertEqual(registry.list_tools()[0]["name"], "demo.echo")
        self.assertEqual(
            registry.get_parameter_schema("demo.echo")["required"], ["value"]
        )

    def test_unknown_tool_is_refused(self) -> None:
        result = ToolRegistry().invoke("missing.tool", {})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error["code"], "TOOL_NOT_FOUND")

    def test_permission_is_checked_before_execution(self) -> None:
        called = False

        def handler(args):
            nonlocal called
            called = True
            return {"echo": args["value"]}

        registry = ToolRegistry(PermissionPolicy(granted=set()))
        registry.register(contract(), handler)
        result = registry.invoke("demo.echo", {"value": "hello"})

        self.assertEqual(result.status, "denied")
        self.assertEqual(result.error["code"], "PERMISSION_DENIED")
        self.assertFalse(called)

    def test_invalid_parameters_are_rejected(self) -> None:
        registry = ToolRegistry()
        registry.register(contract(), lambda args: {"echo": args["value"]})

        result = registry.invoke("demo.echo", {"unexpected": True})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error["code"], "INVALID_ARGUMENTS")
        self.assertIn("required", result.error["message"])

    def test_timeout_and_system_error_are_sanitized(self) -> None:
        registry = ToolRegistry()
        registry.register(
            contract(name="demo.slow", timeout=0.01),
            lambda args: (time.sleep(0.05), {"value": args["value"]})[1],
        )
        timeout_result = registry.invoke("demo.slow", {"value": "x"})

        registry.register(
            contract(name="demo.error"),
            lambda args: (_ for _ in ()).throw(RuntimeError("secret-detail")),
        )
        error_result = registry.invoke("demo.error", {"value": "x"})

        self.assertEqual(timeout_result.status, "timeout")
        self.assertEqual(timeout_result.error["code"], "TOOL_TIMEOUT")
        self.assertEqual(error_result.error["code"], "HANDLER_ERROR")
        self.assertNotIn("secret-detail", error_result.error["message"])

    def test_destructive_tools_cannot_register(self) -> None:
        registry = ToolRegistry()
        with self.assertRaises(ToolRegistrationError):
            registry.register(
                contract(
                    name="files.delete",
                    risk_level="destructive",
                    permissions=("files.write",),
                ),
                lambda args: {},
            )

    def test_write_tool_registers_but_default_policy_denies_execution(self) -> None:
        registry = ToolRegistry()
        registry.register(
            contract(
                name="files.apply",
                risk_level="write",
                permissions=("files.write",),
            ),
            lambda args: {"written": True},
        )

        denied = registry.invoke("files.apply", {"value": "x"})
        allowed = registry.invoke(
            "files.apply",
            {"value": "x"},
            policy=PermissionPolicy({Permission.FILES_WRITE}),
        )

        self.assertEqual(denied.error["code"], "PERMISSION_DENIED")
        self.assertEqual(allowed.status, "success")

    def test_results_are_json_serializable(self) -> None:
        registry = ToolRegistry()
        registry.register(contract(), lambda args: {"echo": args["value"]})
        result = registry.invoke("demo.echo", {"value": "json"})

        encoded = json.dumps(result.to_dict(), allow_nan=False)
        decoded = json.loads(encoded)

        self.assertEqual(decoded["tool"], "demo.echo")
        self.assertEqual(decoded["status"], "success")
        self.assertIsNone(decoded["error"])
        self.assertIsInstance(decoded["duration_ms"], int)

    def test_non_json_handler_result_is_rejected(self) -> None:
        registry = ToolRegistry()
        registry.register(contract(), lambda args: {"bad": {1, 2}})

        result = registry.invoke("demo.echo", {"value": "x"})

        self.assertEqual(result.error["code"], "NON_JSON_RESULT")


if __name__ == "__main__":
    unittest.main()
