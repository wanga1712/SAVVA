"""Default read-only SAVVA tool catalog."""

from .development import DevelopmentInspector
from .software import SoftwareDetector
from .system import SystemInspector


def build_default_registry():
    """Create the isolated default registry without performing diagnostics."""

    from ..core.registry import ToolRegistry
    from ..core.schemas import ToolContract

    registry = ToolRegistry()
    system_inspector = SystemInspector()
    software_detector = SoftwareDetector()
    development_inspector = DevelopmentInspector(software_detector)

    registry.register(
        ToolContract(
            name="system.summary",
            description="Read system hardware information",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            permissions=("system.read",),
            risk_level="read_only",
            timeout_seconds=5,
        ),
        lambda args: system_inspector.summary(),
    )
    registry.register(
        ToolContract(
            name="software.detect",
            description="Detect installed software and its version",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [
                            "python",
                            "pip",
                            "node",
                            "npm",
                            "git",
                            "vscode",
                            "powershell",
                            "winget",
                            "ollama",
                            "docker",
                            "npx",
                            "yarn",
                            "pnpm",
                            "typescript",
                            "vite",
                        ],
                    }
                },
                "additionalProperties": False,
            },
            permissions=("system.read",),
            risk_level="read_only",
            timeout_seconds=30,
        ),
        lambda args: software_detector.detect(args.get("name")),
    )
    registry.register(
        ToolContract(
            name="development.inspect",
            description="Inspect development tools and optional project dependencies",
            parameters={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 32767,
                    }
                },
                "additionalProperties": False,
            },
            permissions=("system.read",),
            risk_level="read_only",
            timeout_seconds=30,
        ),
        lambda args: development_inspector.inspect(args.get("project_path")),
    )
    return registry


__all__ = ["DevelopmentInspector", "SoftwareDetector", "SystemInspector", "build_default_registry"]
