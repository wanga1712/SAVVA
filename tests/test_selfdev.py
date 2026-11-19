from __future__ import annotations

import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from savva.agent.projects import ActionJournal, ProjectService
from savva.agent.selfdev import SelfDevManifest, SelfDevelopmentService
from savva.agent.selfdev_executor import COMMANDS, ControlledWindowsExecutor
from savva.agent.workflow import WorkflowState, WorkflowStore
from savva.core.registry import ToolExecutionError
from savva.agent.config import SettingsError
from savva.server import AppRuntime


ROOT = Path(__file__).resolve().parents[1]


class NeverExecute:
    def run(self, command_id):
        raise AssertionError("Validation must not run before approval.")


class PassingExecutor:
    def __init__(self):
        self.commands = []

    def run(self, command_id):
        self.commands.append(command_id)
        return {"command_id": command_id, "exit_code": 0, "timed_out": False,
                "stdout": "real check output", "stderr": "", "duration_ms": 1}


class SelfDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.manifest = SelfDevManifest.load(ROOT)

    def test_manifest_is_bound_to_candidate_and_protects_stable(self):
        self.assertEqual(self.manifest.branch, "wip/SAVVA-SELFDEV-001")
        self.assertEqual(self.manifest.root, ROOT.resolve())
        self.assertNotEqual(self.manifest.root, self.manifest.stable_root)
        self.assertTrue(self.manifest.document["stable_unavailable"])

    def test_candidate_runtime_pins_workspace_and_state_to_candidate(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,
                {"SAVVA_WORKSPACE_ROOT": str(self.manifest.stable_root)}, clear=False), patch.object(
                    AppRuntime, "_codex_chatgpt_login", return_value=False):
            state = Path(directory)
            runtime = AppRuntime(project_root=ROOT, settings_path=state / "settings.json",
                usage_path=state / "usage.jsonl", action_log_path=state / "actions.jsonl",
                selfdev_manifest=self.manifest)
            self.assertEqual(Path(runtime.settings().workspace_root), ROOT.resolve())
            self.assertEqual(runtime.selfdev_service.manifest.root, ROOT.resolve())
            tool_names = {tool["name"] for tool in runtime.tools()}
            self.assertIn("selfdev.validate", tool_names)
            self.assertNotIn("process.execute", tool_names)
            with self.assertRaises(SettingsError):
                runtime.update_settings({"workspace_root": str(self.manifest.stable_root)})

    def test_executor_uses_fixed_allowlist_and_real_exit_status(self):
        executor = ControlledWindowsExecutor(self.manifest)
        result = executor.run("diagnostics.python_version")
        self.assertIn("Python", result["stdout"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["timed_out"])
        with self.assertRaises(ToolExecutionError) as raised:
            executor.run("powershell arbitrary text")
        self.assertEqual(raised.exception.code, "EXECUTOR_COMMAND_DENIED")
        self.assertEqual(set(COMMANDS), set(self.manifest.document["check_commands"]))

    def test_wip_survives_workflow_store_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "tasks.json"
            journal = ActionJournal(Path(directory) / "actions.jsonl")
            store = WorkflowStore(state_path)
            service = SelfDevelopmentService(self.manifest, NeverExecute(), store,
                ProjectService(ROOT), journal)
            started = service.begin("Refactor SAVVA CSS safely")
            task = WorkflowStore(state_path).get(started["task_id"])
            self.assertEqual(task.state, WorkflowState.DISCOVERY)
            service.discover(task.task_id)
            service.save_wip({"task_id": task.task_id, "brief": "Keep selectors stable.",
                              "findings": ["styles.css is linked by index.html"],
                              "plan": ["Group declarations without changing behavior"]})
            restored = WorkflowStore(state_path).get(task.task_id)
            self.assertEqual(restored.state, WorkflowState.PLANNED)
            self.assertEqual(restored.metadata["wip"]["plan"][0],
                             "Group declarations without changing behavior")

    def test_validation_is_blocked_until_journal_confirms_user_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ActionJournal(Path(directory) / "actions.jsonl")
            store = WorkflowStore(Path(directory) / "tasks.json")
            service = SelfDevelopmentService(self.manifest, NeverExecute(), store,
                ProjectService(ROOT), journal)
            task = store.create("Refactor SAVVA CSS", {"task_type": "REFACTOR",
                "project_root": str(ROOT)}, budget_usd=0.25, max_attempts=1)
            task = store.transition(task.task_id, WorkflowState.DISCOVERY)
            task = store.transition(task.task_id, WorkflowState.ANALYZED)
            task = store.transition(task.task_id, WorkflowState.PLANNED)
            task = store.transition(task.task_id, WorkflowState.WAITING_APPROVAL)
            store.update_metadata(task.task_id, {"proposal_id": "proposal_pending"})
            with self.assertRaises(ToolExecutionError) as raised:
                service.validate(task.task_id)
            self.assertEqual(raised.exception.code, "APPROVAL_REQUIRED")
            self.assertEqual(store.get(task.task_id).state, WorkflowState.WAITING_APPROVAL)

    def test_approved_patch_runs_fixed_checks_and_persists_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ActionJournal(Path(directory) / "actions.jsonl")
            store = WorkflowStore(Path(directory) / "tasks.json")
            executor = PassingExecutor()
            service = SelfDevelopmentService(self.manifest, executor, store,
                ProjectService(ROOT), journal)
            task = store.create("Refactor SAVVA CSS", {"task_type": "REFACTOR",
                "project_root": str(ROOT)}, budget_usd=0.25, max_attempts=1)
            task = store.transition(task.task_id, WorkflowState.DISCOVERY)
            task = store.transition(task.task_id, WorkflowState.ANALYZED)
            task = store.transition(task.task_id, WorkflowState.PLANNED)
            task = store.transition(task.task_id, WorkflowState.WAITING_APPROVAL)
            store.update_metadata(task.task_id, {"proposal_id": "proposal_approved"})
            journal.record(session_id="session123", action="apply", proposal_id="proposal_approved",
                project_path=".", status="applied", paths=["savva/web/styles.css"],
                total_bytes=1, message="Files written after confirmation.")

            result = service.after_approval("proposal_approved")
            self.assertEqual(result["summary"], "PASS")
            self.assertEqual(result["state"], "REVIEW")
            self.assertEqual(len(executor.commands), 4)
            restored = WorkflowStore(Path(directory) / "tasks.json").get(task.task_id)
            self.assertEqual(restored.state, WorkflowState.REVIEW)
            self.assertEqual([item["exit_code"] for item in restored.metadata["test_results"]],
                             [0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
