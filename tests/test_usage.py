from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from savva.agent.usage import UsageLogger


class UsageAccountingTests(unittest.TestCase):
    def test_daily_cost_sums_actual_and_reserved_estimates(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = UsageLogger(Path(directory) / "usage.jsonl")
            logger.record(session_id="one", provider="openrouter", model="m",
                          cost_usd=0.021, cost_verified=True)
            logger.record(session_id="two", provider="openrouter", model="m",
                          cost_usd=0.014, cost_verified=False, cost_estimated=True, status="error")
            self.assertAlmostEqual(logger.daily_cost(), 0.035)
            records = [json.loads(line) for line in logger.path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["cost_verified"] for item in records], [True, False])
            self.assertEqual([item["cost_estimated"] for item in records], [False, True])

    def test_corrupt_usage_log_fails_closed_for_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            path.write_text("{bad-json\n", encoding="utf-8")
            self.assertEqual(UsageLogger(path).daily_cost(), float("inf"))


if __name__ == "__main__":
    unittest.main()
