"""Read-only software discovery with fixed commands and bounded timeouts."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SoftwareSpec:
    executable_names: tuple[str, ...]
    version_args: tuple[str, ...] = ("--version",)


SOFTWARE_SPECS: dict[str, SoftwareSpec] = {
    "python": SoftwareSpec(("python",)),
    "pip": SoftwareSpec(("pip",)),
    "node": SoftwareSpec(("node",)),
    "npm": SoftwareSpec(("npm",)),
    "git": SoftwareSpec(("git",)),
    "vscode": SoftwareSpec(("code",)),
    "powershell": SoftwareSpec(("pwsh", "powershell")),
    "winget": SoftwareSpec(("winget",)),
    "ollama": SoftwareSpec(("ollama",)),
    "docker": SoftwareSpec(("docker",)),
    "npx": SoftwareSpec(("npx",)),
    "yarn": SoftwareSpec(("yarn",)),
    "pnpm": SoftwareSpec(("pnpm",)),
    "typescript": SoftwareSpec(("tsc",)),
    "vite": SoftwareSpec(("vite",)),
}


class SoftwareDetector:
    """Detect known executables and their versions without installing anything."""

    def detect(self, name: str | None = None) -> dict[str, Any]:
        if name is None:
            tools = [self._detect_one(item) for item in SOFTWARE_SPECS]
            return {
                "tools": tools,
                "count": len(tools),
                "installed_count": sum(1 for item in tools if item["installed"]),
            }
        return self._detect_one(name)

    def _detect_one(self, name: str) -> dict[str, Any]:
        if name == "python":
            return self._detect_python()
        if name == "pip":
            return self._detect_pip()

        spec = SOFTWARE_SPECS[name]
        executable = self._find_executable(spec.executable_names)
        if executable is None:
            return self._not_installed(name)
        if name == "ollama":
            return {
                "name": name,
                "installed": True,
                "version": None,
                "verified": False,
                "executable": Path(executable).name,
                "source": "PATH (presence only; executable not launched)",
            }

        args = list(spec.version_args)
        if name == "powershell":
            args = [
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$PSVersionTable.PSVersion.ToString()",
            ]
        version, probe_error = self._run_version([executable, *args])
        return {
            "name": name,
            "installed": True,
            "version": version,
            "verified": version is not None,
            "executable": Path(executable).name,
            "source": "PATH",
            "probe_error": probe_error,
        }

    def _detect_python(self) -> dict[str, Any]:
        executable = sys.executable
        version, probe_error = self._run_version([executable, "--version"])
        return {
            "name": "python",
            "installed": bool(executable),
            "version": version or platform_python_version(),
            "verified": True,
            "executable": Path(executable).name if executable else None,
            "source": "sys.executable",
            "probe_error": probe_error,
        }

    def _detect_pip(self) -> dict[str, Any]:
        try:
            module = importlib.util.find_spec("pip")
        except (ImportError, ValueError):
            module = None
        if module is None:
            return self._not_installed("pip")
        version, probe_error = self._run_version(
            [sys.executable, "-m", "pip", "--version"]
        )
        return {
            "name": "pip",
            "installed": True,
            "version": version,
            "verified": version is not None,
            "executable": Path(sys.executable).name,
            "source": "Python module discovery",
            "probe_error": probe_error,
        }

    def _find_executable(self, names: tuple[str, ...]) -> str | None:
        for name in names:
            path = shutil.which(name)
            if path:
                return path
        return None

    def _run_version(self, command: list[str]) -> tuple[str | None, str | None]:
        executable_path = command[0]
        actual_command = _safe_command(executable_path, command[1:])
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                actual_command,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired:
            return None, "version probe timed out"
        except OSError:
            return None, "version probe failed"
        output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
        if completed.returncode != 0 or not output:
            return None, f"version probe exited with code {completed.returncode}"
        return _extract_version(output), None

    @staticmethod
    def _not_installed(name: str) -> dict[str, Any]:
        return {
            "name": name,
            "installed": False,
            "version": None,
            "verified": False,
            "executable": None,
            "source": "PATH",
            "probe_error": None,
        }


def platform_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _safe_command(executable: str, args: list[str]) -> list[str]:
    # Windows resolves script shims such as npm.CMD through CreateProcess while
    # shell=False keeps command interpretation outside the registry.
    return [executable, *args]


def _extract_version(output: str) -> str:
    first_line = output.splitlines()[0].strip()
    named = re.search(
        r"(?:Python|pip|node|npm|git version|Docker version|PowerShell|winget|v)\s+"
        r"([0-9]+(?:\.[0-9A-Za-z+-]+)+)",
        first_line,
        flags=re.IGNORECASE,
    )
    if named:
        return named.group(1)[:100]
    version = re.search(r"\b([0-9]+(?:\.[0-9A-Za-z+-]+)+)\b", first_line)
    return (version.group(1) if version else first_line)[:100]
