"""Candidate-only project manifest, persisted WIP helpers, and fixed-command executor."""

from __future__ import annotations

import json
import subprocess
import time
import ast
from pathlib import Path
from typing import Any

from ..core.permissions import Permission
from ..core.schemas import ToolContract
from ..core.registry import ToolExecutionError, ToolRegistry
from .projects import ActionJournal, ProjectService
from .workflow import ProjectRouter, WorkflowState, WorkflowStore


MANIFEST_RELATIVE = Path("savva/selfdev/project-manifest.json")
MAX_TOOL_OUTPUT = 64_000


class SelfDevError(ValueError):
    """Safe, user-facing self-development validation error."""


class SelfDevManifest:
    def __init__(self, path: Path):
        self.path = path.resolve(strict=True)
        self.root = self.path.parents[2]
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SelfDevError("The SAVVA self-development manifest is unreadable.") from None
        if not isinstance(document, dict) or document.get("project_id") != "savva-selfdev":
            raise SelfDevError("The SAVVA self-development manifest is invalid.")
        self.document = document
        self.branch = str(document.get("git_branch", ""))
        self.stable_root = (self.root / str(document.get("stable_worktree", "../savva"))).resolve()
        self.discovery_files = tuple(str(item) for item in document.get("discovery_files", []))
        self._validate_root()

    @classmethod
    def load(cls, root: str | Path | None = None) -> "SelfDevManifest":
        candidate = Path(root).expanduser().resolve() if root else Path(__file__).resolve().parents[2]
        return cls(candidate / MANIFEST_RELATIVE)

    def _validate_root(self) -> None:
        if not self.branch or not self.root.is_dir():
            raise SelfDevError("The Candidate root or branch is invalid.")
        if self.root == self.stable_root or self.root in self.stable_root.parents or self.stable_root in self.root.parents:
            raise SelfDevError("Candidate and Stable must be separate worktrees.")
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.root,
                             shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=10, check=False)
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=self.root,
                                shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=10, check=False)
        if top.returncode or Path(top.stdout.strip()).resolve() != self.root:
            raise SelfDevError("The Candidate directory is not the repository worktree root.")
        if branch.returncode or branch.stdout.strip() != self.branch:
            raise SelfDevError("The current Git branch does not match the Candidate manifest.")

    def public(self) -> dict[str, Any]:
        return {key: self.document[key] for key in (
            "project_id", "git_branch", "architecture_rules", "check_commands",
            "allowed_operations", "protected_working_directory", "stable_unavailable",
            "engineering_standard",
        ) if key in self.document}


class SelfDevelopmentService:
    def __init__(self, manifest: SelfDevManifest, executor: Any,
                 workflows: WorkflowStore, projects: ProjectService, journal: ActionJournal):
        self.manifest, self.executor, self.workflows = manifest, executor, workflows
        self.projects, self.journal = projects, journal

    def register_tools(self, registry: ToolRegistry) -> None:
        _register(registry, "selfdev.begin", "Start a persisted Candidate WIP and record the current base SHA.",
                  _schema({"request": {"type": "string", "minLength": 8, "maxLength": 2000}}, ["request"]),
                  lambda args: self.begin(args["request"]), 5)
        _register(registry, "selfdev.discover", "Read only manifest-approved files for the active WIP.",
                  _schema({"task_id": _id_schema()}, ["task_id"]),
                  lambda args: self.discover(args["task_id"]), 10)
        _register(registry, "selfdev.resume", "Read a persisted self-development WIP after backend restart.",
                  _schema({"task_id": _id_schema()}, ["task_id"]),
                  lambda args: self.resume(args["task_id"]), 5)
        _register(registry, "selfdev.save_wip", "Persist a concise architecture brief, findings, and plan.",
                  _schema({"task_id": _id_schema(), "brief": {"type": "string", "maxLength": 2000},
                           "findings": _array_schema(), "plan": _array_schema()},
                          ["task_id", "brief", "findings", "plan"]),
                  self.save_wip, 5)
        _register(registry, "selfdev.record_patch", "Bind the exact pending files.propose diff to the active WIP.",
                  _schema({"task_id": _id_schema(), "proposal_id": _id_schema()}, ["task_id", "proposal_id"]),
                  self.record_patch, 5)
        _register(registry, "selfdev.validate", "After approval only, run real fixed Candidate checks and persist logs.",
                  _schema({"task_id": _id_schema()}, ["task_id"]),
                  lambda args: self.validate(args["task_id"]), 420, Permission.SELFDEV_VALIDATE)

    def begin(self, request: str) -> dict[str, Any]:
        self.manifest._validate_root()
        route = ProjectRouter({"savva-selfdev": self.manifest.root}).route(request, "savva-selfdev")
        route["task_type"] = "REFACTOR"
        task = self.workflows.create(request, route, budget_usd=0.25, max_attempts=1)
        base = _git(self.manifest.root, "rev-parse", "HEAD")
        self.workflows.transition(task.task_id, WorkflowState.DISCOVERY, result="Self-development WIP started.")
        self.workflows.update_metadata(task.task_id, {
            "project_id": "savva-selfdev", "base_sha": base, "branch": self.manifest.branch,
            "wip": {}, "proposal_id": None, "test_results": [],
        })
        return {"task_id": task.task_id, "state": "DISCOVERY", "base_sha": base,
                "project": "savva-selfdev", "stable_writable": False}

    def discover(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id, {WorkflowState.DISCOVERY})
        files: dict[str, str] = {}
        for relative in self.manifest.discovery_files:
            path = _candidate_file(self.manifest.root, relative)
            if not path.is_file() or path.stat().st_size > 65_536:
                raise ToolExecutionError("DISCOVERY_FILE_INVALID", "A manifest discovery file is unavailable or too large.")
            files[relative] = path.read_text(encoding="utf-8")
        components = {}
        for relative, expected in {
            "savva/agent/projects.py": "ProjectService",
            "savva/agent/workflow.py": "WorkflowStore",
            "savva/core/registry.py": "ToolRegistry",
            "savva/agent/orchestrator.py": "AgentOrchestrator",
        }.items():
            source = _candidate_file(self.manifest.root, relative).read_text(encoding="utf-8")
            components[expected] = any(isinstance(node, ast.ClassDef) and node.name == expected
                                       for node in ast.walk(ast.parse(source)))
        return {"task_id": task.task_id, "state": task.state,
                "manifest": self.manifest.public(), "files": files,
                "existing_components": components}

    def resume(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id, set(WorkflowState))
        return {"task_id": task.task_id, "state": task.state, "request": task.request,
                "history": task.history, "wip": task.metadata,
                "stable_writable": False}

    def save_wip(self, args: dict[str, Any]) -> dict[str, Any]:
        task = self._task(args["task_id"], {WorkflowState.DISCOVERY})
        self.workflows.transition(task.task_id, WorkflowState.ANALYZED, result="Candidate architecture discovery recorded.")
        self.workflows.update_metadata(task.task_id, {"wip": {
            "brief": args["brief"], "findings": args["findings"][:20], "plan": args["plan"][:20],
            "updated_at": time.time(),
        }})
        self.workflows.transition(task.task_id, WorkflowState.PLANNED, result=args["brief"])
        return {"task_id": task.task_id, "state": "WIP", "persisted": True,
                "findings": len(args["findings"][:20]), "plan_steps": len(args["plan"][:20])}

    def record_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        task = self._task(args["task_id"], {WorkflowState.PLANNED})
        proposal = self.projects.get_public(args["proposal_id"])
        diff = self.projects.render_diff(args["proposal_id"])
        self.workflows.transition(task.task_id, WorkflowState.WAITING_APPROVAL,
                                  result="Awaiting explicit user approval of the exact diff.")
        self.workflows.update_metadata(task.task_id, {"proposal_id": args["proposal_id"],
            "stage": "PATCH",
            "proposal_summary": proposal["summary"], "proposal_fingerprint": diff["fingerprint"],
            "proposal_diff": diff["diff"][:MAX_TOOL_OUTPUT]})
        return {"task_id": task.task_id, "state": "APPROVAL", "proposal": proposal,
                "diff": diff["diff"][:MAX_TOOL_OUTPUT], "confirmation_required": True}

    def validate(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id, {WorkflowState.WAITING_APPROVAL})
        proposal_id = task.metadata.get("proposal_id")
        approved = any(item.get("proposal_id") == proposal_id and item.get("action") == "apply"
                       and item.get("status") == "applied" for item in self.journal.recent(300))
        if not approved:
            raise ToolExecutionError("APPROVAL_REQUIRED", "This WIP proposal has not been explicitly approved and applied.")
        self.workflows.transition(task.task_id, WorkflowState.CODING, result="Approved Candidate patch detected.")
        self.workflows.transition(task.task_id, WorkflowState.VALIDATING, result="Starting fixed real checks.")
        results = []
        for command in ("python.syntax", "selfdev.quality"):
            result = self.executor.run(command)
            results.append(result)
            if result["exit_code"] != 0 or result["timed_out"]:
                break
        if all(item["exit_code"] == 0 and not item["timed_out"] for item in results):
            for command in ("python.unittest", "git.diff_check"):
                results.append(self.executor.run(command))
        passed = all(item["exit_code"] == 0 and not item["timed_out"] for item in results)
        self.workflows.update_metadata(task.task_id, {"test_results": results,
            "validation_status": "PASS" if passed else "FAIL", "review_required": True})
        target = WorkflowState.REVIEW if passed else WorkflowState.FAILED
        self.workflows.transition(task.task_id, target,
            result="All Candidate checks passed." if passed else "Candidate checks failed; see stored exit codes and logs.")
        return {"task_id": task.task_id, "state": target.value,
                "checks": results, "summary": "PASS" if passed else "FAIL"}

    def after_approval(self, proposal_id: str) -> dict[str, Any] | None:
        for task in self.workflows.list_tasks(str(self.manifest.root)):
            if task.metadata.get("proposal_id") == proposal_id and task.state == WorkflowState.WAITING_APPROVAL:
                return self.validate(task.task_id)
        return None

    def _task(self, task_id: str, states: set[WorkflowState]):
        task = self.workflows.get(task_id)
        if task is None or task.project_root != str(self.manifest.root):
            raise ToolExecutionError("WIP_NOT_FOUND", "The Candidate WIP was not found.")
        if WorkflowState(task.state) not in states:
            raise ToolExecutionError("WIP_STATE_INVALID", "The Candidate WIP is not at the required workflow stage.")
        return task


def _register(registry: ToolRegistry, name: str, description: str, parameters: dict[str, Any],
              handler, timeout: int, permission: Permission = Permission.WORKSPACE_READ):
    registry.register(ToolContract(name=name, description=description, parameters=parameters,
        permissions=(permission.value,),
        risk_level="read_only", timeout_seconds=timeout), handler)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _array_schema() -> dict[str, Any]:
    return {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 1000}}


def _id_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 8, "maxLength": 100}


def _candidate_file(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or path.drive or ".." in path.parts or any(part.lower() in {".git", ".env", "workspace"} for part in path.parts):
        raise ToolExecutionError("CANDIDATE_PATH_DENIED", "Path is outside the manifest-approved Candidate files.")
    target = (root / path).resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError:
        raise ToolExecutionError("CANDIDATE_PATH_DENIED", "Path resolves outside the Candidate worktree.") from None
    return target


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, shell=False, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.strip()

\n