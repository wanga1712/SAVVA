from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from savva.agent.models import ModelAdapterError
from savva.agent.recovery import FailureCategory, RecoveryController


class RecoveryControllerTests(unittest.TestCase):
    def test_classifies_primary_and_provider_failures(self):
        self.assertEqual(RecoveryController.classify(
            "codex_chatgpt", ModelAdapterError("CODEX_QUOTA_EXHAUSTED", "safe", failure_kind="quota")),
            FailureCategory.PRIMARY_QUOTA_EXHAUSTED)
        self.assertEqual(RecoveryController.classify(
            "codex_chatgpt", ModelAdapterError("CODEX_LOGIN", "safe", failure_kind="auth")),
            FailureCategory.PRIMARY_AUTH_FAILED)
        self.assertEqual(RecoveryController.classify(
            "openrouter", ModelAdapterError("MODEL_CONNECTION_ERROR", "safe")),
            FailureCategory.PROVIDER_UNREACHABLE)
        self.assertEqual(RecoveryController.classify(
            "codex_chatgpt", ModelAdapterError("CODEX_NOT_INSTALLED", "safe")),
            FailureCategory.MODEL_UNAVAILABLE)

    def test_journal_persists_safe_metadata_without_prompt_or_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.jsonl"
            controller = RecoveryController(path)
            error = ModelAdapterError("MODEL_CONNECTION_ERROR", "private prompt never logged", failure_kind="transient")
            event = controller.record(operation_id="operation-12345678", provider="codex_chatgpt",
                                      model="codex-default", error=error)
            persisted = path.read_text(encoding="utf-8")
            self.assertEqual(event["category"], FailureCategory.PRIMARY_DISCONNECTED.value)
            self.assertNotIn("private prompt", persisted)
            self.assertNotIn("api_key", persisted.lower())
            restarted = RecoveryController(path)
            self.assertEqual(restarted.recent()[0]["operation_id"], "operation-12345678")

    def test_recovery_attempts_are_bounded_and_cooled_down(self):
        controller = RecoveryController(max_attempts=2, cooldown_seconds=60)
        self.assertTrue(controller.mark_attempt("op"))
        self.assertFalse(controller.mark_attempt("op"))
        controller._last_attempt["op"] -= 61
        self.assertTrue(controller.mark_attempt("op"))
        controller._last_attempt["op"] -= 61
        self.assertFalse(controller.mark_attempt("op"))


if __name__ == "__main__":
    unittest.main()
