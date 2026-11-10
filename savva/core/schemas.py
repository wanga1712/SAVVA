"""JSON-compatible contracts and results shared by every SAVVA tool."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


def validate_parameters(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON Schema subset used by SAVVA tool contracts."""

    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not _matches_type(value, expected_type):
        errors.append(f"{path}: expected {expected_type}")
        return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not one of the allowed values")

    if expected_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: required property is missing")
        for key, item in value.items():
            if key in properties:
                errors.extend(validate_parameters(item, properties[key], f"{path}.{key}"))
            elif schema.get("additionalProperties", False) is False:
                errors.append(f"{path}.{key}: additional property is not allowed")

    if expected_type == "array" and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate_parameters(item, schema["items"], f"{path}[{index}]"))

    if expected_type == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")

    if expected_type in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above {schema['maximum']}")

    return errors


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return False


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: str
    description: str
    parameters: dict[str, Any]
    permissions: tuple[str, ...]
    risk_level: str
    timeout_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": deepcopy(self.parameters),
            "permissions": list(self.permissions),
            "risk_level": self.risk_level,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: str
    status: str
    data: dict[str, Any] | None
    error: dict[str, Any] | None
    duration_ms: int

    @classmethod
    def success(cls, tool: str, data: dict[str, Any], duration_ms: int) -> "ToolResult":
        return cls(tool=tool, status="success", data=data, error=None, duration_ms=duration_ms)

    @classmethod
    def failure(
        cls,
        tool: str,
        status: str,
        code: str,
        message: str,
        duration_ms: int,
    ) -> "ToolResult":
        return cls(
            tool=tool,
            status=status,
            data=None,
            error={"code": code, "message": message},
            duration_ms=duration_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "data": deepcopy(self.data),
            "error": deepcopy(self.error),
            "duration_ms": self.duration_ms,
        }
