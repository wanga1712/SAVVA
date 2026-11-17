from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from savva.agent.workflow import ProjectRouter, WorkflowState, WorkflowStore


class WorkflowTests(unittest.TestCase):
    def test_router_requires_registered_project_and_classifies_bugfix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = ProjectRouter({"demo": directory})
            route = router.route("Fix the broken parser", "demo")
            self.assertEqual(route["task_type"], "BUGFIX")
            self.assertTrue(route["needs_discovery"])
            with self.assertRaises(ValueError):
                router.route("fix it", "unknown")

    def test_workflow_transitions_persist_and_reject_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory) / "tasks.json")
            route = ProjectRouter({}).route("Audit this code")
            task = store.create("Audit this code", route, budget_usd=1.0, max_attempts=2)
            task = store.transition(task.task_id, WorkflowState.DISCOVERY)
            task = store.transition(task.task_id, WorkflowState.ANALYZED)
            self.assertEqual(store.get(task.task_id).state, WorkflowState.ANALYZED)
            with self.assertRaises(ValueError):
                store.transition(task.task_id, WorkflowState.DONE)

    def test_repair_attempt_limit_is_enforced_after_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory) / "tasks.json")
            task = store.create("Fix a bug", ProjectRouter({}).route("Fix a bug"),
                                budget_usd=1.0, max_attempts=1)
            for state in (WorkflowState.DISCOVERY, WorkflowState.ANALYZED,
                          WorkflowState.PLANNED, WorkflowState.CODING,
                          WorkflowState.VALIDATING, WorkflowState.REPAIR):
                task = store.transition(task.task_id, state)
            self.assertEqual(store.get(task.task_id).attempts, 1)
            for state in (WorkflowState.CODING, WorkflowState.VALIDATING,
                          WorkflowState.REPAIR):
                if state == WorkflowState.REPAIR:
                    with self.assertRaises(ValueError):
                        store.transition(task.task_id, state)
                else:
                    task = store.transition(task.task_id, state)


if __name__ == "__main__":
    unittest.main()
