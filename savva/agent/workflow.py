"""Persistent task routing and lifecycle primitives for development workflows."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class TaskType(StrEnum):
    QUESTION = "QUESTION"
    ANALYSIS = "ANALYSIS"
    BUGFIX = "BUGFIX"
    FEATURE = "FEATURE"
    REFACTOR = "REFACTOR"
    NEW_PROJECT = "NEW_PROJECT"


class WorkflowState(StrEnum):
    NEW = "NEW"
    DISCOVERY = "DISCOVERY"
    ANALYZED = "ANALYZED"
    PLANNED = "PLANNED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    CODING = "CODING"
    VALIDATING = "VALIDATING"
    REVIEW = "REVIEW"
    REPAIR = "REPAIR"
    DONE = "DONE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


TRANSITIONS = {
    WorkflowState.NEW: {WorkflowState.DISCOVERY, WorkflowState.CANCELLED},
    WorkflowState.DISCOVERY: {WorkflowState.ANALYZED, WorkflowState.BLOCKED, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.ANALYZED: {WorkflowState.PLANNED, WorkflowState.BLOCKED, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.PLANNED: {WorkflowState.WAITING_APPROVAL, WorkflowState.CODING, WorkflowState.BLOCKED, WorkflowState.CANCELLED},
    WorkflowState.WAITING_APPROVAL: {WorkflowState.CODING, WorkflowState.CANCELLED, WorkflowState.BLOCKED},
    WorkflowState.CODING: {WorkflowState.VALIDATING, WorkflowState.BLOCKED, WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.VALIDATING: {WorkflowState.REVIEW, WorkflowState.REPAIR, WorkflowState.FAILED, WorkflowState.BLOCKED, WorkflowState.CANCELLED},
    WorkflowState.REPAIR: {WorkflowState.CODING, WorkflowState.FAILED, WorkflowState.BLOCKED, WorkflowState.CANCELLED},
    WorkflowState.REVIEW: {WorkflowState.DONE, WorkflowState.REPAIR, WorkflowState.BLOCKED, WorkflowState.FAILED},
    WorkflowState.DONE: set(), WorkflowState.FAILED: set(),
    WorkflowState.BLOCKED: set(), WorkflowState.CANCELLED: set(),
}


@dataclass(slots=True)
class WorkflowTask:
    task_id: str
    request: str
    task_type: str
    project_root: str | None
    state: str = WorkflowState.NEW
    budget_usd: float = 0.25
    max_attempts: int = 2
    attempts: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ProjectRouter:
    """Classify task intent and resolve only explicitly registered project roots."""

    def __init__(self, projects: dict[str, str | Path]):
        self.projects = {name: Path(path).expanduser().resolve() for name, path in projects.items()}

    def route(self, request: str, project: str | None = None) -> dict[str, Any]:
        text = request.lower()
        kind = TaskType.QUESTION
        for task_type, words in (
            (TaskType.NEW_PROJECT, ("new project", "create project")),
            (TaskType.REFACTOR, ("refactor", "restructure")),
            (TaskType.BUGFIX, ("bug", "fix", "broken", "error", "ошибк", "исправ")),
            (TaskType.FEATURE, ("implement", "add ", "feature", "реализ", "добав")),
            (TaskType.ANALYSIS, ("analyze", "audit", "review", "обслед", "аудит")),
        ):
            if any(word in text for word in words):
                kind = task_type
                break
        root = self.projects.get(project) if project else None
        if project and root is None:
            raise ValueError("Project is not registered.")
        return {"task_type": kind.value, "project": project, "project_root": str(root) if root else None,
                "needs_discovery": kind in {TaskType.BUGFIX, TaskType.FEATURE, TaskType.REFACTOR, TaskType.ANALYSIS},
                "roles": ["Explorer", "Architect", "Developer", "Repair", "Reviewer"] if kind in {TaskType.BUGFIX, TaskType.FEATURE, TaskType.REFACTOR} else ["Explorer"]}


class WorkflowStore:
    """Small atomic JSON task store; records workflow metadata, never prompts from agents."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def create(self, request: str, route: dict[str, Any], *, budget_usd: float, max_attempts: int) -> WorkflowTask:
        if not request.strip() or budget_usd <= 0 or max_attempts < 1:
            raise ValueError("Request, positive budget, and at least one attempt are required.")
        task = WorkflowTask(uuid.uuid4().hex, request, route["task_type"], route.get("project_root"),
                            budget_usd=budget_usd, max_attempts=max_attempts)
        task.history.append({"state": task.state, "at": task.created_at})
        self._save(task)
        return task

    def get(self, task_id: str) -> WorkflowTask | None:
        with self._lock:
            doc = self._read().get(task_id)
            return WorkflowTask(**doc) if doc else None

    def list_tasks(self, project_root: str | None = None) -> list[WorkflowTask]:
        with self._lock:
            tasks = [WorkflowTask(**item) for item in self._read().values() if isinstance(item, dict)]
        if project_root is not None:
            tasks = [task for task in tasks if task.project_root == project_root]
        return sorted(tasks, key=lambda task: task.updated_at, reverse=True)

    def transition(self, task_id: str, state: WorkflowState, *, result: str | None = None) -> WorkflowTask:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(task_id)
            current = WorkflowState(task.state)
            if state not in TRANSITIONS[current]:
                raise ValueError(f"Illegal workflow transition: {current} -> {state}.")
            if state == WorkflowState.REPAIR and task.attempts >= task.max_attempts:
                raise ValueError("The workflow repair-attempt limit was reached.")
            if state == WorkflowState.REPAIR:
                task.attempts += 1
            task.state, task.updated_at = state, time.time()
            event: dict[str, Any] = {"state": state, "at": task.updated_at}
            if result is not None:
                event["result"] = result[:2000]
            task.history.append(event)
            self._save(task)
            return task

    def update_metadata(self, task_id: str, changes: dict[str, Any]) -> WorkflowTask:
        """Persist bounded workflow-specific WIP fields without changing its state graph."""
        if not isinstance(changes, dict) or len(changes) > 32:
            raise ValueError("Workflow metadata must be a small object.")
        encoded = json.dumps(changes, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 256_000:
            raise ValueError("Workflow metadata exceeds its storage limit.")
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(task_id)
            task.metadata.update(json.loads(encoded))
            task.updated_at = time.time()
            self._save(task)
            return task

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, task: WorkflowTask) -> None:
        with self._lock:
            document = self._read()
            document[task.task_id] = asdict(task)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
