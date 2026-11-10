"""Agent-safe registry bound to one confined workspace."""

from __future__ import annotations

from pathlib import Path

from ..core.registry import ToolRegistry
from ..core.schemas import ToolContract
from ..tools.development import DevelopmentInspector
from ..tools.software import SOFTWARE_SPECS, SoftwareDetector
from ..tools.system import SystemInspector
from .workspace import SafeWorkspace


def build_agent_registry(workspace_root: str | Path) -> ToolRegistry:
    registry = ToolRegistry()
    workspace = SafeWorkspace(workspace_root)
    system = SystemInspector()
    software = SoftwareDetector()
    development = DevelopmentInspector(software)

    registry.register(
        ToolContract(
            name="system.summary",
            description=(
                "Read operating system, CPU, RAM, GPU, and disk information. "
                "Use this for hardware readiness questions."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            permissions=("system.read",),
            risk_level="read_only",
            timeout_seconds=5,
        ),
        lambda args: system.summary(),
    )
    registry.register(
        ToolContract(
            name="software.detect",
            description=(
                "Detect one installed development tool and its version. Use name=all "
                "or omit name to inspect every supported tool."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [*SOFTWARE_SPECS, "all"],
                    }
                },
                "additionalProperties": False,
            },
            permissions=("system.read",),
            risk_level="read_only",
            timeout_seconds=30,
        ),
        lambda args: software.detect(
            None if args.get("name") == "all" else args.get("name")
        ),
    )
    registry.register(
        ToolContract(
            name="development.inspect",
            description=(
                "Inspect Python, pip, Node.js, npm, Git, web tooling, and dependencies "
                "in the configured safe workspace. Returns deterministic readiness."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            permissions=("system.read", "workspace.read"),
            risk_level="read_only",
            timeout_seconds=30,
        ),
        lambda args: development.inspect(str(workspace.root)),
    )
    registry.register(
        ToolContract(
            name="workspace.list",
            description=(
                "List files and directories inside the configured safe workspace. "
                "Use relative paths only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "maxLength": 500},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "additionalProperties": False,
            },
            permissions=("workspace.read",),
            risk_level="read_only",
            timeout_seconds=5,
        ),
        lambda args: workspace.list_directory(
            args.get("path", "."), args.get("limit", 200)
        ),
    )
    registry.register(
        ToolContract(
            name="workspace.read",
            description=(
                "Read an explicitly allowed UTF-8 project text file from the safe "
                "workspace. The maximum read is 65536 bytes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65536,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            permissions=("workspace.read",),
            risk_level="read_only",
            timeout_seconds=5,
        ),
        lambda args: workspace.read_text(
            args["path"], args.get("max_bytes", 65536)
        ),
    )
    return registry
