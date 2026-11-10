"""Controlled project proposals, diffs, validation, and confirmed writes."""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.registry import ToolExecutionError
from .project_templates import build_savva_store_project
from .workspace import SafeWorkspace

MAX_FILES_PER_PROPOSAL = 80
MAX_FILE_BYTES = 262_144
MAX_TOTAL_BYTES = 1_048_576
MAX_DIFF_CHARS = 240_000
MAX_TREE_ENTRIES = 2_000
PROPOSAL_TTL_SECONDS = 3_600
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
BLOCKED_COMPONENTS = {
    ".git",
    ".svn",
    ".hg",
    ".ssh",
    ".env",
    "node_modules",
}
ALLOWED_WRITE_EXTENSIONS = {
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
ALLOWED_WRITE_FILENAMES = {".gitignore", "Dockerfile", "Makefile"}
FILENAME_PATTERN = re.compile(r"^[^<>:\"|?*\x00-\x1f]+$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SECRET_PATTERNS = (
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


@dataclass(frozen=True, slots=True)
class PlannedChange:
    path: str
    content: str
    expected_sha256: str | None
    before_sha256: str | None
    before_exists: bool

    @property
    def size_bytes(self) -> int:
        return len(self.content.encode("utf-8"))

    @property
    def after_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def action(self) -> str:
        return "update" if self.before_exists else "create"


@dataclass(slots=True)
class ProposalRecord:
    proposal_id: str
    session_id: str | None
    summary: str
    project_path: str
    changes: tuple[PlannedChange, ...]
    fingerprint: str
    confirmation_token: str
    created_at: float
    status: str = "pending"
    validate_structure: bool = False
    error: str | None = None


@dataclass(slots=True)
class ActionRecord:
    timestamp_unix: float
    session_id: str
    action: str
    proposal_id: str
    project_path: str
    status: str
    paths: list[str]
    total_bytes: int
    message: str


class ActionJournal:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        *,
        session_id: str,
        action: str,
        proposal_id: str,
        project_path: str,
        status: str,
        paths: list[str],
        total_bytes: int,
        message: str,
    ) -> None:
        record = ActionRecord(
            timestamp_unix=time.time(),
            session_id=session_id,
            action=action,
            proposal_id=proposal_id,
            project_path=project_path,
            status=status,
            paths=list(paths[:MAX_FILES_PER_PROPOSAL]),
            total_bytes=max(0, int(total_bytes)),
            message=message[:500],
        )
        document = {
            "timestamp_unix": record.timestamp_unix,
            "session_id": record.session_id,
            "action": record.action,
            "proposal_id": record.proposal_id,
            "project_path": record.project_path,
            "status": record.status,
            "paths": record.paths,
            "total_bytes": record.total_bytes,
            "message": record.message,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(document, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-max(1, limit):]
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records


class ProjectService:
    """Workspace-relative proposals with one-time, hash-bound confirmation."""

    def __init__(self, workspace_root: str | Path, journal: ActionJournal | None = None):
        self.workspace = SafeWorkspace(workspace_root)
        self.root = self.workspace.root
        self.journal = journal
        self._proposals: dict[str, ProposalRecord] = {}
        self._lock = threading.RLock()

    def inspect_tree(self, path: str = ".", depth: int = 3) -> dict[str, Any]:
        target = self._existing_path(path, allow_root=True)
        if not target.is_dir():
            raise ToolExecutionError("PROJECT_NOT_DIRECTORY", "Path is not a directory.")
        maximum_depth = min(max(depth, 0), 5)
        entries: list[dict[str, Any]] = []
        truncated = False

        def walk(directory: Path, current_depth: int) -> None:
            nonlocal truncated
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                return
            for child in children:
                if len(entries) >= MAX_TREE_ENTRIES:
                    truncated = True
                    return
                if self._is_reparse_point(child):
                    continue
                try:
                    relative = child.relative_to(self.root).as_posix()
                    if child.is_dir():
                        entries.append({"path": relative, "type": "directory"})
                        if current_depth < maximum_depth:
                            walk(child, current_depth + 1)
                    elif child.is_file():
                        entries.append(
                            {
                                "path": relative,
                                "type": "file",
                                "size_bytes": child.stat().st_size,
                            }
                        )
                except OSError:
                    continue

        walk(target, 0)
        return {
            "root": str(self.root),
            "path": target.relative_to(self.root).as_posix() or ".",
            "entries": entries,
            "truncated": truncated,
        }

    def read_file(self, path: str, max_bytes: int = 65_536) -> dict[str, Any]:
        target = self._existing_path(path)
        if not target.is_file():
            raise ToolExecutionError("FILES_NOT_FILE", "Path is not a regular file.")
        if self._is_reparse_point(target):
            raise ToolExecutionError("FILES_PATH_DENIED", "Reparse points are not allowed.")
        result = self.workspace.read_text(path, max_bytes)
        result["sha256"] = self._file_hash(target)
        return result

    def create_template_proposal(
        self,
        *,
        project_name: str,
        title: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_name = project_name.strip().lower()
        if not PROJECT_NAME_PATTERN.fullmatch(normalized_name):
            raise ToolExecutionError(
                "PROJECT_NAME_INVALID",
                "Project name must be 2-64 lowercase letters, digits, or hyphens.",
            )
        project_target = self._write_target(normalized_name, require_file=False)
        if project_target.exists():
            if not project_target.is_dir() or self._is_reparse_point(project_target):
                raise ToolExecutionError(
                    "PROJECT_TARGET_INVALID",
                    "The project target is not a safe directory.",
                )
            if any(project_target.iterdir()):
                raise ToolExecutionError(
                    "PROJECT_TARGET_NOT_EMPTY",
                    "The project directory already contains files.",
                )
        files = build_savva_store_project(normalized_name, title.strip()[:120])
        changes = [
            {
                "path": path,
                "content": content,
                "expected_sha256": None,
            }
            for path, content in files.items()
        ]
        return self._create_proposal(
            session_id=session_id,
            summary=f"Create React + TypeScript storefront in {normalized_name}",
            project_path=normalized_name,
            changes=changes,
            validate_structure=True,
        )

    def propose(
        self,
        *,
        summary: str,
        project_path: str,
        changes: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        project_dir = self._normalize_relative(
            project_path, allow_root=True, require_file=False
        )
        normalized_changes: list[dict[str, Any]] = []
        for change in changes:
            if not isinstance(change, dict):
                raise ToolExecutionError("PROPOSAL_INVALID", "Each change must be an object.")
            relative = change.get("path")
            if not isinstance(relative, str):
                raise ToolExecutionError("PROPOSAL_INVALID", "Each change needs a path.")
            combined = f"{project_dir}/{relative}" if project_dir else relative
            normalized_changes.append({**change, "path": combined})
        return self._create_proposal(
            session_id=session_id,
            summary=summary.strip()[:500],
            project_path=project_dir,
            changes=normalized_changes,
            validate_structure=False,
        )

    def get_public(self, proposal_id: str, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            proposal = self._get_proposal(proposal_id, session_id)
            return self._public_proposal(proposal)

    def get_confirmation(
        self, proposal_id: str, session_id: str
    ) -> dict[str, Any]:
        with self._lock:
            proposal = self._get_proposal(proposal_id, session_id)
            if proposal.status != "pending":
                raise ToolExecutionError(
                    "PROPOSAL_NOT_PENDING", "This proposal is no longer pending."
                )
            return {
                **self._public_proposal(proposal),
                "confirmation_token": proposal.confirmation_token,
                "diff": self._render_diff(proposal),
            }

    def bind_session(self, proposal_id: str, session_id: str) -> None:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                return
            if proposal.session_id is None:
                proposal.session_id = session_id
            elif proposal.session_id != session_id:
                raise ToolExecutionError(
                    "PROPOSAL_SESSION_MISMATCH",
                    "Proposal belongs to another chat session.",
                )

    def render_diff(self, proposal_id: str, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            proposal = self._get_proposal(proposal_id, session_id)
            return {
                "proposal_id": proposal.proposal_id,
                "fingerprint": proposal.fingerprint,
                "diff": self._render_diff(proposal),
            }

    def validate(
        self,
        *,
        proposal_id: str | None = None,
        project_path: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if proposal_id:
            with self._lock:
                proposal = self._get_proposal(proposal_id, session_id)
                files = {
                    item.path: item.content
                    for item in proposal.changes
                }
                return self._validate_project_files(
                    proposal.project_path, files, proposal.proposal_id
                )
        if not project_path:
            raise ToolExecutionError(
                "VALIDATION_TARGET_MISSING",
                "Provide proposal_id or project_path.",
            )
        project_dir = self._existing_path(project_path, allow_root=True)
        if not project_dir.is_dir():
            raise ToolExecutionError("PROJECT_NOT_DIRECTORY", "Path is not a directory.")
        files: dict[str, str] = {}
        for directory, directories, filenames in os.walk(project_dir, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not self._is_reparse_point(Path(directory) / name)
            ]
            for filename in filenames:
                if len(files) >= 200:
                    break
                child = Path(directory) / filename
                if not child.is_file() or self._is_reparse_point(child):
                    continue
                relative = child.relative_to(project_dir).as_posix()
                if child.stat().st_size <= 300_000:
                    try:
                        files[relative] = child.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue
        return self._validate_project_files(project_path, files, None)

    def apply(
        self,
        *,
        proposal_id: str,
        confirmation_token: str,
        session_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            proposal = self._get_proposal(proposal_id, session_id)
            if proposal.status != "pending":
                raise ToolExecutionError(
                    "PROPOSAL_NOT_PENDING", "This confirmation was already used."
                )
            if not hmac.compare_digest(proposal.confirmation_token, confirmation_token):
                raise ToolExecutionError(
                    "CONFIRMATION_INVALID", "Confirmation token does not match."
                )
            if time.time() - proposal.created_at > PROPOSAL_TTL_SECONDS:
                proposal.status = "expired"
                raise ToolExecutionError("PROPOSAL_EXPIRED", "Proposal has expired.")
            try:
                self._revalidate_current_state(proposal)
                if proposal.validate_structure:
                    validation = self._validate_project_files(
                        proposal.project_path,
                        {item.path: item.content for item in proposal.changes},
                        proposal.proposal_id,
                    )
                    if not validation["valid"]:
                        raise ToolExecutionError(
                            "PROJECT_VALIDATION_FAILED",
                            "Proposed project failed structural validation.",
                        )
                proposal.status = "applying"
                self._commit_changes(proposal)
                proposal.status = "applied"
            except Exception as exc:
                proposal.status = "failed"
                proposal.error = (
                    exc.safe_message
                    if isinstance(exc, ToolExecutionError)
                    else "The proposal could not be applied."
                )
                self._journal(
                    session_id,
                    "apply",
                    proposal,
                    "error",
                    proposal.error,
                )
                raise
            self._journal(
                session_id,
                "apply",
                proposal,
                "applied",
                "Files written after confirmation.",
            )
            return self._public_proposal(proposal)

    def reject(
        self,
        *,
        proposal_id: str,
        confirmation_token: str,
        session_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            proposal = self._get_proposal(proposal_id, session_id)
            if proposal.status != "pending":
                raise ToolExecutionError(
                    "PROPOSAL_NOT_PENDING", "This proposal is no longer pending."
                )
            if not hmac.compare_digest(proposal.confirmation_token, confirmation_token):
                raise ToolExecutionError(
                    "CONFIRMATION_INVALID", "Confirmation token does not match."
                )
            proposal.status = "rejected"
            self._journal(
                session_id,
                "reject",
                proposal,
                "rejected",
                "Proposal rejected; no files were changed.",
            )
            return self._public_proposal(proposal)

    def _create_proposal(
        self,
        *,
        session_id: str | None,
        summary: str,
        project_path: str,
        changes: list[dict[str, Any]],
        validate_structure: bool,
    ) -> dict[str, Any]:
        if not changes:
            raise ToolExecutionError("PROPOSAL_EMPTY", "A proposal needs at least one file.")
        if len(changes) > MAX_FILES_PER_PROPOSAL:
            raise ToolExecutionError(
                "PROPOSAL_TOO_LARGE",
                f"A proposal may contain at most {MAX_FILES_PER_PROPOSAL} files.",
            )
        planned: list[PlannedChange] = []
        seen: set[str] = set()
        total_bytes = 0
        for change in changes:
            relative = change.get("path")
            content = change.get("content")
            expected = change.get("expected_sha256")
            if not isinstance(relative, str) or not isinstance(content, str):
                raise ToolExecutionError(
                    "PROPOSAL_INVALID", "Each change needs string path and content."
                )
            normalized = self._normalize_relative(relative)
            if normalized.lower() in seen:
                raise ToolExecutionError(
                    "PROPOSAL_DUPLICATE_PATH", "Proposal contains duplicate paths."
                )
            seen.add(normalized.lower())
            if expected is not None and (
                not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected)
            ):
                raise ToolExecutionError(
                    "EXPECTED_HASH_INVALID", "expected_sha256 must be lowercase SHA-256."
                )
            encoded = content.encode("utf-8")
            if b"\x00" in encoded:
                raise ToolExecutionError("FILE_CONTENT_INVALID", "Binary content is not allowed.")
            if len(encoded) > MAX_FILE_BYTES:
                raise ToolExecutionError(
                    "FILE_TOO_LARGE", f"{normalized} exceeds {MAX_FILE_BYTES} bytes."
                )
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                raise ToolExecutionError(
                    "SECRET_MATERIAL_BLOCKED",
                    "Proposed content contains credential-like secret material.",
                )
            total_bytes += len(encoded)
            target = self._write_target(normalized)
            exists = target.exists()
            if exists:
                if not target.is_file() or self._is_reparse_point(target):
                    raise ToolExecutionError(
                        "FILES_PATH_DENIED", "Only regular files may be updated."
                    )
                before_hash = self._file_hash(target)
                if expected is None:
                    raise ToolExecutionError(
                        "EXPECTED_HASH_REQUIRED",
                        f"{normalized} exists; expected_sha256 is required.",
                    )
                if expected != before_hash:
                    raise ToolExecutionError(
                        "STALE_PROPOSAL",
                        f"{normalized} changed before the proposal was created.",
                    )
            else:
                if expected is not None:
                    raise ToolExecutionError(
                        "STALE_PROPOSAL", f"{normalized} no longer exists."
                    )
                before_hash = None
            planned.append(
                PlannedChange(
                    path=normalized,
                    content=content,
                    expected_sha256=expected,
                    before_sha256=before_hash,
                    before_exists=exists,
                )
            )
        if total_bytes > MAX_TOTAL_BYTES:
            raise ToolExecutionError(
                "PROPOSAL_TOO_LARGE",
                f"Proposal exceeds {MAX_TOTAL_BYTES} total bytes.",
            )
        canonical = [
            {
                "path": item.path,
                "before": item.before_sha256,
                "after": item.after_sha256,
            }
            for item in sorted(planned, key=lambda item: item.path.lower())
        ]
        fingerprint = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        proposal = ProposalRecord(
            proposal_id="proposal_" + secrets.token_hex(16),
            session_id=session_id,
            summary=summary,
            project_path=project_path,
            changes=tuple(planned),
            fingerprint=fingerprint,
            confirmation_token=secrets.token_urlsafe(32),
            created_at=time.time(),
            validate_structure=validate_structure,
        )
        with self._lock:
            self._prune()
            self._proposals[proposal.proposal_id] = proposal
            public = self._public_proposal(proposal)
            self._journal(
                session_id or "unbound",
                "propose",
                proposal,
                "pending",
                "Proposal prepared; no files were written.",
            )
            return public

    def _revalidate_current_state(self, proposal: ProposalRecord) -> None:
        for change in proposal.changes:
            target = self._write_target(change.path)
            if change.before_exists:
                if not target.is_file() or self._is_reparse_point(target):
                    raise ToolExecutionError(
                        "STALE_PROPOSAL", f"{change.path} is no longer a regular file."
                    )
                if self._file_hash(target) != change.before_sha256:
                    raise ToolExecutionError(
                        "STALE_PROPOSAL", f"{change.path} changed after proposal creation."
                    )
            elif target.exists():
                raise ToolExecutionError(
                    "STALE_PROPOSAL", f"{change.path} was created by another process."
                )

    def _commit_changes(self, proposal: ProposalRecord) -> None:
        backups: dict[Path, bytes] = {}
        created_paths: list[Path] = []
        created_dirs: list[Path] = []
        staged: list[tuple[PlannedChange, Path, Path]] = []
        try:
            for change in proposal.changes:
                target = self._write_target(change.path)
                missing_dirs: list[Path] = []
                parent = target.parent
                while parent != self.root and not parent.exists():
                    missing_dirs.append(parent)
                    parent = parent.parent
                target.parent.mkdir(parents=True, exist_ok=True)
                created_dirs.extend(reversed(missing_dirs))
                self._assert_safe_existing_ancestors(target.parent)
                temporary = self._stage_file(target.parent, change.content.encode("utf-8"))
                staged.append((change, target, temporary))
            self._revalidate_current_state(proposal)
            for change, target, temporary in staged:
                if change.before_exists:
                    backups[target] = target.read_bytes()
                    os.replace(temporary, target)
                else:
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        raise ToolExecutionError(
                            "STALE_PROPOSAL",
                            f"{change.path} was created by another process.",
                        ) from None
                    finally:
                        temporary.unlink(missing_ok=True)
                    created_paths.append(target)
        except Exception:
            for target, content in backups.items():
                try:
                    temporary = self._stage_file(target.parent, content)
                    os.replace(temporary, target)
                except OSError:
                    pass
            for target in reversed(created_paths):
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        finally:
            for _, _, temporary in staged:
                temporary.unlink(missing_ok=True)

    def _stage_file(self, parent: Path, content: bytes) -> Path:
        descriptor, raw_path = tempfile.mkstemp(prefix=".savva-", suffix=".tmp", dir=parent)
        path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def _render_diff(self, proposal: ProposalRecord) -> str:
        chunks: list[str] = []
        for change in sorted(proposal.changes, key=lambda item: item.path.lower()):
            target = self._write_target(change.path)
            before = ""
            from_path = "/dev/null"
            if change.before_exists:
                from_path = f"a/{change.path}"
                try:
                    before = target.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    before = ""
            diff = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    change.content.splitlines(keepends=True),
                    fromfile=from_path,
                    tofile=f"b/{change.path}",
                    lineterm="\n",
                )
            )
            chunks.append(diff)
        document = "\n".join(chunks)
        if len(document) > MAX_DIFF_CHARS:
            return document[:MAX_DIFF_CHARS] + "\n... diff truncated ...\n"
        return document

    @staticmethod
    def _validate_project_files(
        project_path: str,
        files: dict[str, str],
        proposal_id: str | None,
    ) -> dict[str, Any]:
        normalized: dict[str, str] = {}
        prefix = project_path.strip("/") + "/" if project_path.strip("/") else ""
        for path, content in files.items():
            relative = path[len(prefix):] if prefix and path.startswith(prefix) else path
            normalized[relative] = content
        required = [
            "package.json",
            "index.html",
            "src/main.tsx",
            "src/App.tsx",
            "src/styles.css",
            "src/data/computers.ts",
        ]
        checks: list[dict[str, Any]] = []
        missing = [path for path in required if path not in normalized]
        checks.append(
            {
                "name": "required_files",
                "passed": not missing,
                "details": "All required files are present."
                if not missing
                else "Missing: " + ", ".join(missing),
            }
        )
        package_ok = False
        package_detail = "package.json is missing or invalid."
        try:
            package = json.loads(normalized["package.json"])
            scripts = package.get("scripts", {})
            dependencies = package.get("dependencies", {})
            package_ok = (
                isinstance(scripts, dict)
                and "dev" in scripts
                and "build" in scripts
                and isinstance(dependencies, dict)
                and "react" in dependencies
            )
            if package_ok:
                package_detail = "React, dev, and build configuration found."
        except (KeyError, json.JSONDecodeError, TypeError):
            pass
        checks.append(
            {"name": "package_configuration", "passed": package_ok, "details": package_detail}
        )
        product_source = normalized.get("src/data/computers.ts", "")
        product_ok = all(name in product_source for name in ("SAVVA GAME", "SAVVA HOME", "SAVVA AI"))
        checks.append(
            {
                "name": "product_catalog",
                "passed": product_ok,
                "details": "GAME, HOME, and AI catalog entries found."
                if product_ok
                else "Catalog is missing one or more SAVVA series.",
            }
        )
        app_source = normalized.get("src/App.tsx", "")
        structure_ok = all(
            token in app_source
            for token in ("SiteHeader", "Hero", "CatalogSection", "ApproachSection", "ContactSection")
        )
        checks.append(
            {
                "name": "page_structure",
                "passed": structure_ok,
                "details": "Main page sections are wired."
                if structure_ok
                else "One or more main page sections are missing.",
            }
        )
        return {
            "valid": all(check["passed"] for check in checks),
            "proposal_id": proposal_id,
            "project_path": project_path,
            "checks": checks,
        }

    def _public_proposal(self, proposal: ProposalRecord) -> dict[str, Any]:
        changes = [
            {
                "path": item.path,
                "action": item.action,
                "size_bytes": item.size_bytes,
                "before_sha256": item.before_sha256,
                "after_sha256": item.after_sha256,
            }
            for item in proposal.changes
        ]
        return {
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "summary": proposal.summary,
            "project_path": proposal.project_path,
            "fingerprint": proposal.fingerprint,
            "file_count": len(changes),
            "total_bytes": sum(item["size_bytes"] for item in changes),
            "changes": changes,
            "created_at_unix": proposal.created_at,
            "error": proposal.error,
        }

    def _get_proposal(
        self, proposal_id: str, session_id: str | None
    ) -> ProposalRecord:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ToolExecutionError("PROPOSAL_NOT_FOUND", "Proposal was not found.")
        if proposal.session_id and session_id and proposal.session_id != session_id:
            raise ToolExecutionError(
                "PROPOSAL_SESSION_MISMATCH",
                "Proposal belongs to another chat session.",
            )
        return proposal

    def _journal(
        self,
        session_id: str,
        action: str,
        proposal: ProposalRecord,
        status: str,
        message: str,
    ) -> None:
        if self.journal:
            self.journal.record(
                session_id=session_id,
                action=action,
                proposal_id=proposal.proposal_id,
                project_path=proposal.project_path,
                status=status,
                paths=[item.path for item in proposal.changes],
                total_bytes=sum(item.size_bytes for item in proposal.changes),
                message=message,
            )

    def _prune(self) -> None:
        now = time.time()
        for proposal_id, proposal in list(self._proposals.items()):
            if now - proposal.created_at > PROPOSAL_TTL_SECONDS and proposal.status == "pending":
                proposal.status = "expired"
            elif len(self._proposals) > 200 and proposal.status != "pending":
                self._proposals.pop(proposal_id, None)

    def _normalize_relative(
        self,
        path: str,
        allow_root: bool = False,
        require_file: bool = True,
    ) -> str:
        if not isinstance(path, str) or "\x00" in path:
            raise ToolExecutionError("FILES_PATH_DENIED", "Invalid relative path.")
        text = path.strip().replace("\\", "/")
        if not text or text == ".":
            if allow_root:
                return "."
            raise ToolExecutionError("FILES_PATH_DENIED", "A file path is required.")
        candidate = Path(text)
        if candidate.is_absolute() or candidate.drive:
            raise ToolExecutionError("FILES_PATH_DENIED", "Absolute paths are not allowed.")
        parts = [part for part in text.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            raise ToolExecutionError("FILES_PATH_DENIED", "Parent traversal is not allowed.")
        for part in parts:
            if len(part) > 120 or not FILENAME_PATTERN.fullmatch(part):
                raise ToolExecutionError("FILES_PATH_DENIED", "Invalid Windows path component.")
            if part.endswith((" ", ".")) or part.upper().split(".", 1)[0] in WINDOWS_RESERVED:
                raise ToolExecutionError("FILES_PATH_DENIED", "Windows reserved name is not allowed.")
            if part.lower() in BLOCKED_COMPONENTS or part.lower().startswith(".env"):
                raise ToolExecutionError(
                    "FILES_PATH_DENIED", "Sensitive or generated directories are blocked."
                )
        normalized = "/".join(parts)
        if not require_file:
            return normalized
        filename = parts[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_WRITE_EXTENSIONS and filename not in ALLOWED_WRITE_FILENAMES:
            raise ToolExecutionError(
                "FILES_TYPE_NOT_ALLOWED",
                "This file type is not writable by the project builder.",
            )
        return normalized

    def _write_target(self, relative: str, require_file: bool = True) -> Path:
        normalized = self._normalize_relative(relative, require_file=require_file)
        target = self.root.joinpath(*normalized.split("/"))
        try:
            target.relative_to(self.root)
        except ValueError:
            raise ToolExecutionError("FILES_PATH_DENIED", "Path escapes the workspace.") from None
        self._assert_safe_existing_ancestors(target.parent)
        return target

    def _existing_path(self, relative: str, allow_root: bool = False) -> Path:
        normalized = self._normalize_relative(
            relative, allow_root=allow_root, require_file=False
        )
        target = (
            self.root
            if normalized == "."
            else self._write_target(normalized, require_file=False)
        )
        try:
            target.resolve(strict=True).relative_to(self.root)
        except (OSError, ValueError):
            raise ToolExecutionError("FILES_PATH_DENIED", "Path escapes the workspace.") from None
        current = self.root
        for part in normalized.split("/") if normalized != "." else []:
            current = current / part
            if self._is_reparse_point(current):
                raise ToolExecutionError(
                    "FILES_PATH_DENIED", "Symlinks and junctions are not allowed."
                )
        return target

    def _assert_safe_existing_ancestors(self, parent: Path) -> None:
        current = self.root
        try:
            relative_parts = parent.relative_to(self.root).parts
        except ValueError:
            raise ToolExecutionError("FILES_PATH_DENIED", "Path escapes the workspace.") from None
        for part in relative_parts:
            current = current / part
            if not current.exists():
                break
            if self._is_reparse_point(current):
                raise ToolExecutionError(
                    "FILES_PATH_DENIED", "Symlinks and junctions are not allowed."
                )
            if not current.is_dir():
                raise ToolExecutionError(
                    "FILES_PATH_DENIED", "A file blocks the requested directory."
                )

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                return True
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
            return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        except OSError:
            return False

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
