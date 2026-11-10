"""Development-environment inspection and project dependency comparison."""

from __future__ import annotations

import importlib.metadata
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from ..core.registry import ToolExecutionError
from .software import SOFTWARE_SPECS, SoftwareDetector

CORE_TOOLS = ("python", "pip", "node", "npm", "git")
WEB_TOOLS = ("npx", "yarn", "pnpm", "typescript", "vite")
OTHER_TOOLS = ("vscode", "powershell", "winget", "ollama", "docker")
MAX_PROJECT_FILE_BYTES = 1_000_000
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class DevelopmentInspector:
    def __init__(self, software: SoftwareDetector):
        self.software = software

    def inspect(self, project_path: str | None = None) -> dict[str, Any]:
        detected = [self.software.detect(name) for name in SOFTWARE_SPECS]
        by_name = {item["name"]: item for item in detected}
        core = {name: by_name[name] for name in CORE_TOOLS}
        web = {name: by_name[name] for name in WEB_TOOLS}
        other = {name: by_name[name] for name in OTHER_TOOLS}
        project = self._inspect_project(project_path) if project_path else None
        readiness = self._readiness(core, web, project)
        return {
            "core_tools": core,
            "web_tools": web,
            "other_tools": other,
            "readiness": readiness,
            "project": project,
        }

    @staticmethod
    def _readiness(
        core: dict[str, dict[str, Any]],
        web: dict[str, dict[str, Any]],
        project: dict[str, Any] | None,
    ) -> dict[str, Any]:
        required = ["node", "npm", "git"]
        missing_required = [name for name in required if not core[name]["installed"]]
        missing_recommended = [
            name for name in ("npx", "typescript", "vite") if not web[name]["installed"]
        ]
        report: list[str] = []
        if missing_required:
            report.append(
                "Install required tools: " + ", ".join(missing_required)
            )
        else:
            report.append("Node.js, npm, and Git are available.")
        if not web["npx"]["installed"]:
            report.append("npx is unavailable; npm normally provides it.")
        if project and project["declared_dependencies"] and project["missing_dependencies"]:
            missing = ", ".join(
                item["name"] for item in project["missing_dependencies"]
            )
            report.append("Project dependencies are not installed: " + missing)
        if project and project["declared_dependencies"] and not project["missing_dependencies"]:
            report.append("Declared project dependencies are present.")
        if project and not project["declared_dependencies"]:
            report.append("No project dependency declarations were found.")
        return {
            "scenario": "react_typescript",
            "status": "ready" if not missing_required else "needs_setup",
            "missing_required": missing_required,
            "missing_recommended": missing_recommended,
            "report": report,
        }

    def _inspect_project(self, raw_path: str) -> dict[str, Any]:
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise ToolExecutionError(
                "PROJECT_PATH_INVALID", "The project path does not exist."
            ) from None
        if not path.is_dir():
            raise ToolExecutionError(
                "PROJECT_PATH_INVALID", "The project path must be a directory."
            )

        declarations: list[dict[str, Any]] = []
        read_errors: list[dict[str, str]] = []
        files_read: list[str] = []

        package_path = path / "package.json"
        if package_path.is_file():
            package = self._read_json(package_path, read_errors)
            if package is not None:
                files_read.append("package.json")
                self._collect_node_declarations(package, declarations)

        requirements_path = path / "requirements.txt"
        if requirements_path.is_file():
            content = self._read_text(requirements_path, read_errors)
            if content is not None:
                files_read.append("requirements.txt")
                self._collect_requirement_lines(content, declarations)

        pyproject_path = path / "pyproject.toml"
        if pyproject_path.is_file():
            content = self._read_text(pyproject_path, read_errors)
            if content is not None:
                files_read.append("pyproject.toml")
                try:
                    self._collect_pyproject(tomllib.loads(content), declarations)
                except tomllib.TOMLDecodeError:
                    read_errors.append(
                        {"file": "pyproject.toml", "error": "invalid TOML"}
                    )

        unique_declarations = {
            (item["ecosystem"], _normalize_name(item["name"])): item
            for item in declarations
        }
        declarations = sorted(
            unique_declarations.values(),
            key=lambda item: (item["ecosystem"], item["name"].lower()),
        )
        installed: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for item in declarations:
            version = (
                self._installed_python_version(item["name"])
                if item["ecosystem"] == "python"
                else self._installed_node_version(path, item["name"])
            )
            record = {
                "ecosystem": item["ecosystem"],
                "name": item["name"],
                "version": version,
            }
            if version is None:
                missing.append(record)
            else:
                installed.append(record)

        return {
            "path": str(path),
            "files_read": files_read,
            "declared_dependencies": declarations,
            "installed_dependencies": installed,
            "missing_dependencies": missing,
            "read_errors": read_errors,
        }

    @staticmethod
    def _read_text(path: Path, errors: list[dict[str, str]]) -> str | None:
        if path.is_symlink():
            errors.append({"file": path.name, "error": "symbolic links are not allowed"})
            return None
        try:
            if path.stat().st_size > MAX_PROJECT_FILE_BYTES:
                errors.append({"file": path.name, "error": "file is too large"})
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append({"file": path.name, "error": "file could not be read"})
            return None

    def _read_json(self, path: Path, errors: list[dict[str, str]]) -> Any:
        content = self._read_text(path, errors)
        if content is None:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            errors.append({"file": path.name, "error": "invalid JSON"})
            return None

    @staticmethod
    def _collect_node_declarations(
        package: Any, declarations: list[dict[str, Any]]
    ) -> None:
        if not isinstance(package, dict):
            return
        for group in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            dependencies = package.get(group)
            if not isinstance(dependencies, dict):
                continue
            for name, specifier in dependencies.items():
                if _valid_package_name(name):
                    declarations.append(
                        {
                            "ecosystem": "node",
                            "name": name,
                            "specifier": str(specifier),
                            "source": f"package.json:{group}",
                        }
                    )

    @staticmethod
    def _collect_requirement_lines(
        content: str, declarations: list[dict[str, Any]]
    ) -> None:
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if not match:
                continue
            specifier_match = re.search(r"(\[[^\]]*\])?([<>=!~][^;\s]*)", line)
            declarations.append(
                {
                    "ecosystem": "python",
                    "name": match.group(1),
                    "specifier": specifier_match.group(2) if specifier_match else "",
                    "source": "requirements.txt",
                }
            )

    @staticmethod
    def _collect_pyproject(
        document: dict[str, Any], declarations: list[dict[str, Any]]
    ) -> None:
        project = document.get("project")
        if not isinstance(project, dict):
            return
        groups: list[tuple[str, Any]] = [("project.dependencies", project.get("dependencies", []))]
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            groups.extend(
                (f"project.optional-dependencies.{group}", values)
                for group, values in optional.items()
            )
        for source, values in groups:
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str):
                    continue
                match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", value)
                if match:
                    remaining = value[match.end():].strip()
                    declarations.append(
                        {
                            "ecosystem": "python",
                            "name": match.group(1),
                            "specifier": remaining,
                            "source": source,
                        }
                    )

    @staticmethod
    def _installed_python_version(name: str) -> str | None:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    @staticmethod
    def _installed_node_version(project_path: Path, name: str) -> str | None:
        if not _valid_package_name(name):
            return None
        package_json = project_path / "node_modules" / Path(*name.split("/")) / "package.json"
        try:
            if package_json.stat().st_size > MAX_PROJECT_FILE_BYTES:
                return None
            document = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        version = document.get("version") if isinstance(document, dict) else None
        return str(version) if version else None


def _normalize_name(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _valid_package_name(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    if name.startswith("@"):
        parts = name.split("/")
        return (
            len(parts) == 2
            and bool(PACKAGE_NAME.match(parts[0][1:]))
            and bool(PACKAGE_NAME.match(parts[1]))
        )
    return bool(PACKAGE_NAME.match(name))
