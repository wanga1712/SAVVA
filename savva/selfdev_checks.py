"""Static Candidate checks; this module does not import or execute project modules."""

from __future__ import annotations

import ast
import difflib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def inspect(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    py_files = sorted([*root.joinpath("savva").rglob("*.py"), *root.joinpath("tests").rglob("*.py")])
    syntax_errors: list[str] = []
    modules: dict[Path, ast.Module] = {}
    for path in py_files:
        try:
            modules[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            syntax_errors.append(f"{path.relative_to(root).as_posix()}: {type(exc).__name__}")

    changed = _changed_paths(root)
    changed_python = [root / name for name in changed if name.endswith(".py") and (root / name).is_file()]
    long_modules = []
    preexisting_long_modules = []
    for path in changed_python:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines <= 300:
            continue
        name = path.relative_to(root).as_posix()
        previous = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=root, shell=False,
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
        old_lines = len(previous.stdout.splitlines()) if previous.returncode == 0 else 0
        item = {"path": name, "lines": lines, "baseline_lines": old_lines}
        (preexisting_long_modules if old_lines > 300 else long_modules).append(item)
    import_errors, import_count = _check_imports(root, modules)
    forbidden = []
    for name in changed:
        path = root / name
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            forbidden.append(name)
        if any(part.lower() in {".git", ".env", "arduino_lab"} for part in Path(name).parts):
            forbidden.append(name)
    secret_files = _scan_secrets(root, changed)
    duplicates, similar = _class_review(root, modules)
    blockers = bool(syntax_errors or long_modules or import_errors or forbidden or secret_files)
    return {"status": "FAIL" if blockers else "PASS", "syntax_errors": syntax_errors,
            "modules_over_300_lines": long_modules, "import_errors": import_errors,
            "preexisting_modules_over_300_lines": preexisting_long_modules,
            "local_import_edges_checked": import_count, "forbidden_paths": sorted(set(forbidden)),
            "secret_files": secret_files, "duplicate_class_names": duplicates,
            "similar_class_names_for_review": similar,
            "architecture_review_required": bool(duplicates or similar),
            "changed_paths_checked": len(changed)}


def _changed_paths(root: Path) -> list[str]:
    changed: set[str] = set()
    for args in (("diff", "HEAD", "--name-only", "--"),
                 ("ls-files", "--others", "--exclude-standard")):
        result = subprocess.run(["git", *args], cwd=root, shell=False, capture_output=True,
                                stdin=subprocess.DEVNULL, text=True, timeout=10, check=False)
        if result.returncode == 0:
            changed.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(changed)


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_exists(root: Path, name: str) -> bool:
    parts = name.split(".")
    base = root.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _check_imports(root: Path, modules: dict[Path, ast.Module]) -> tuple[list[str], int]:
    errors: list[str] = []
    edges = 0
    for path, tree in modules.items():
        current = _module_name(root, path)
        package = current if path.name == "__init__.py" else current.rpartition(".")[0]
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names if alias.name == "savva" or alias.name.startswith("savva."))
            elif isinstance(node, ast.ImportFrom) and node.level:
                parts = package.split(".") if package else []
                keep = max(0, len(parts) - node.level + 1)
                base_parts = parts[:keep]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
                if base == "savva" or base.startswith("savva."):
                    targets.append(base)
                    if node.module is None:
                        targets.extend(base + "." + alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and (node.module == "savva" or (node.module or "").startswith("savva.")):
                targets.append(node.module or "")
            for target in targets:
                edges += 1
                if not _module_exists(root, target):
                    errors.append(f"{path.relative_to(root).as_posix()}:{node.lineno} -> {target}")
    return errors, edges


def _scan_secrets(root: Path, changed: list[str]) -> list[str]:
    found: list[str] = []
    for name in changed:
        path = root / name
        if not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            found.append(name)
    return found


def _class_review(root: Path, modules: dict[Path, ast.Module]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    definitions: dict[str, list[str]] = {}
    for path, tree in modules.items():
        if "tests" in path.relative_to(root).parts:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                definitions.setdefault(node.name, []).append(path.relative_to(root).as_posix())
    duplicates = [{"class": name, "files": files} for name, files in definitions.items() if len(files) > 1]
    names = sorted(definitions)
    similar = []
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            if difflib.SequenceMatcher(None, left.lower(), right.lower()).ratio() >= 0.9:
                similar.append({"classes": [left, right], "files": [*definitions[left], *definitions[right]]})
    return duplicates[:100], similar[:100]


def main() -> int:
    result = inspect()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
