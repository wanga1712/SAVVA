"""Confined read-only access to a configured project workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.registry import ToolExecutionError

ALLOWED_TEXT_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
ALLOWED_FILENAMES = {".gitignore", "Dockerfile", "Makefile"}


class SafeWorkspace:
    def __init__(self, root: str | Path):
        resolved = Path(root).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        if not resolved.is_dir():
            raise ValueError("Workspace root must be a directory.")
        self.root = resolved

    def list_directory(self, relative_path: str = ".", limit: int = 200) -> dict[str, Any]:
        target = self._resolve(relative_path)
        if not target.is_dir():
            raise ToolExecutionError("WORKSPACE_NOT_DIRECTORY", "Path is not a directory.")
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(target.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            raise ToolExecutionError("WORKSPACE_READ_ERROR", "Directory could not be read.") from None
        for child in children[: min(max(1, limit), 500)]:
            if child.is_symlink():
                continue
            try:
                entry_type = "directory" if child.is_dir() else "file"
                size = child.stat().st_size if entry_type == "file" else None
            except OSError:
                continue
            entries.append(
                {
                    "path": child.relative_to(self.root).as_posix(),
                    "type": entry_type,
                    "size_bytes": size,
                }
            )
        return {
            "root": str(self.root),
            "path": target.relative_to(self.root).as_posix() or ".",
            "entries": entries,
            "truncated": len(children) > len(entries),
        }

    def read_text(self, relative_path: str, max_bytes: int = 65536) -> dict[str, Any]:
        target = self._resolve(relative_path)
        if not target.is_file() or target.is_symlink():
            raise ToolExecutionError("WORKSPACE_NOT_FILE", "Path is not a regular file.")
        if (
            target.suffix.lower() not in ALLOWED_TEXT_EXTENSIONS
            and target.name not in ALLOWED_FILENAMES
        ):
            raise ToolExecutionError(
                "WORKSPACE_TYPE_NOT_ALLOWED", "This file type is not readable by the agent."
            )
        byte_limit = min(max(1, max_bytes), 65536)
        try:
            size = target.stat().st_size
            if size > byte_limit:
                raise ToolExecutionError(
                    "WORKSPACE_FILE_TOO_LARGE",
                    f"File exceeds the {byte_limit}-byte read limit.",
                )
            content = target.read_text(encoding="utf-8")
        except UnicodeError:
            raise ToolExecutionError("WORKSPACE_NOT_TEXT", "File is not valid UTF-8 text.") from None
        except OSError:
            raise ToolExecutionError("WORKSPACE_READ_ERROR", "File could not be read.") from None
        return {
            "path": target.relative_to(self.root).as_posix(),
            "size_bytes": size,
            "content": content,
        }

    def _resolve(self, relative_path: str) -> Path:
        raw = Path(relative_path or ".")
        if raw.is_absolute() or raw.drive:
            raise ToolExecutionError("WORKSPACE_PATH_DENIED", "Absolute paths are not allowed.")
        if ".." in raw.parts:
            raise ToolExecutionError("WORKSPACE_PATH_DENIED", "Parent traversal is not allowed.")
        candidate = (self.root / raw).resolve(strict=True)
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ToolExecutionError(
                "WORKSPACE_PATH_DENIED", "Path is outside the safe workspace."
            ) from None
        return candidate
