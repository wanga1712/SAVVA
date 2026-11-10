"""Agent-safe registry bound to one confined workspace."""

from __future__ import annotations

from pathlib import Path

from ..core.registry import ToolRegistry
from ..core.schemas import ToolContract
from ..tools.development import DevelopmentInspector
from ..tools.software import SOFTWARE_SPECS, SoftwareDetector
from ..tools.system import SystemInspector
from .workspace import SafeWorkspace
from .projects import ProjectService


def build_agent_registry(
    workspace_root: str | Path,
    project_service: ProjectService | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    workspace = SafeWorkspace(workspace_root)
    projects = project_service or ProjectService(workspace_root)
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
            name="project.inspect",
            description=(
                "Inspect the bounded file tree of the configured SAVVA workspace. "
                "Symlinks and junctions are skipped."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "maxLength": 500},
                    "depth": {"type": "integer", "minimum": 0, "maximum": 5},
                },
                "additionalProperties": False,
            },
            permissions=("workspace.read",),
            risk_level="read_only",
            timeout_seconds=5,
        ),
        lambda args: projects.inspect_tree(
            args.get("path", "."), args.get("depth", 3)
        ),
    )
    registry.register(
        ToolContract(
            name="files.read",
            description=(
                "Read an allowed UTF-8 file inside the SAVVA workspace. Returns the "
                "current SHA-256 required for safe updates."
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
        lambda args: projects.read_file(
            args["path"], args.get("max_bytes", 65536)
        ),
    )
    registry.register(
        ToolContract(
            name="project.create",
            description=(
                "Prepare a complete React + TypeScript + Vite SAVVA computer store "
                "project as a proposal. This never writes files before user approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 64,
                        "pattern": "[a-z0-9][a-z0-9-]{1,63}",
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                },
                "required": ["project_name", "title"],
                "additionalProperties": False,
            },
            permissions=("workspace.read",),
            risk_level="read_only",
            timeout_seconds=10,
        ),
        lambda args: projects.create_template_proposal(
            project_name=args["project_name"],
            title=args["title"],
        ),
    )
    registry.register(
        ToolContract(
            name="files.propose",
            description=(
                "Prepare new or updated UTF-8 files inside a workspace project as a "
                "proposal. Existing files require expected_sha256 from files.read. "
                "No file is written before explicit user approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                    "project_path": {"type": "string", "minLength": 1, "maxLength": 300},
                    "changes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 80,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "minLength": 1, "maxLength": 500},
                                "content": {"type": "string", "maxLength": 262144},
                                "expected_sha256": {
                                    "type": "string",
                                    "pattern": "[a-f0-9]{64}",
                                },
                            },
                            "required": ["path", "content"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "project_path", "changes"],
                "additionalProperties": False,
            },
            permissions=("workspace.read",),
            risk_level="read_only",
            timeout_seconds=10,
        ),
        lambda args: projects.propose(
            summary=args["summary"],
            project_path=args["project_path"],
            changes=args["changes"],
        ),
    )
    registry.register(
        ToolContract(
            name="files.diff",
            description=(
                "Return the unified diff for a pending proposal. Reading a diff never "
                "writes files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "minLength": 10, "maxLength": 100}
                },
                "required": ["proposal_id"],
                "additionalProperties": False,
            },
            permissions=("workspace.read",),
            risk_level="read_only",
            timeout_seconds=5,
        ),
        lambda args: projects.render_diff(args["proposal_id"]),
    )
    registry.register(
        ToolContract(
            name="files.apply",
            description=(
                "Request user confirmation to write an existing proposal. The model "
                "cannot execute this tool directly; the local UI must approve it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "minLength": 10, "maxLength": 100}
                },
                "required": ["proposal_id"],
                "additionalProperties": False,
            },
            permissions=("files.write",),
            risk_level="write",
            timeout_seconds=5,
        ),
        lambda args: (_ for _ in ()).throw(
            ToolExecutionError(
                "CONFIRMATION_REQUIRED",
                "File writes require explicit confirmation through the local UI.",
            )
        ),
    )
    registry.register(
        ToolContract(
            name="project.validate",
            description=(
                "Validate a proposed or existing React project's required files, "
                "package configuration, catalog, and page structure."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "minLength": 10, "maxLength": 100},
                    "project_path": {"type": "string", "minLength": 1, "maxLength": 300},
                },
                "additionalProperties": False,
            },
            permissions=("workspace.read",),
            risk_level="read_only",
            timeout_seconds=10,
        ),
        lambda args: projects.validate(
            proposal_id=args.get("proposal_id"),
            project_path=args.get("project_path"),
        ),
    )
    return registry
