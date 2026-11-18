from __future__ import annotations

import unittest
from unittest.mock import patch

from savva.agent.models import ModelAdapterError, OpenRouterAdapter


class FallbackBudgetTests(unittest.TestCase):
    def test_estimate_includes_system_history_tools_and_output_ceiling(self):
        adapter = OpenRouterAdapter(model="test/model", base_url="https://openrouter.ai/api/v1",
                                    api_key="test-key")
        history = [{"role": "system", "content": "System policy"},
                   {"role": "user", "content": "Short question"}]
        tools = [{"type": "function", "function": {"name": "files.propose", "description": "x"}}]
        with patch("savva.agent.models._openrouter_model_price", return_value=(1e-6, 2e-6)):
            small = adapter.max_cost_estimate(history, [], 100)
            with_tool_schema = adapter.max_cost_estimate(history, tools, 100)
            larger_output = adapter.max_cost_estimate(history, tools, 1000)
        self.assertGreater(with_tool_schema, small)
        self.assertGreater(larger_output, with_tool_schema)

    def test_missing_provider_cost_is_not_marked_verified(self):
        adapter = OpenRouterAdapter(model="test/model", base_url="https://openrouter.ai/api/v1",
                                    api_key="test-key")
        result = adapter._parse_response({"choices": [{"message": {"content": "ok"}}],
                                          "usage": {"prompt_tokens": 4, "completion_tokens": 2,
                                                    "total_tokens": 6}})
        self.assertFalse(result.usage.cost_verified)
        self.assertTrue(result.usage.token_counts_verified)
        self.assertEqual(result.usage.cost_usd, 0)

    def test_complete_token_usage_is_costed_from_confirmed_rate_card(self):
        class Transport:
            def post_json(self, *args, **kwargs):
                return {"choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        adapter = OpenRouterAdapter(model="test/model", base_url="https://openrouter.ai/api/v1",
                                    api_key="test-key", transport=Transport())
        with patch("savva.agent.models._openrouter_model_price", return_value=(0.001, 0.002)):
            adapter.max_cost_estimate([{"role": "system", "content": "policy"}], [], 64)
            result = adapter.complete([{"role": "system", "content": "policy"}], [],
                                      max_tokens=64, timeout_seconds=1)
        self.assertAlmostEqual(result.usage.cost_usd, 0.02)
        self.assertFalse(result.usage.cost_verified)
        self.assertTrue(result.usage.cost_estimated)

    def test_nonfinite_price_is_rejected(self):
        adapter = OpenRouterAdapter(model="test/model", base_url="https://openrouter.ai/api/v1",
                                    api_key="test-key")
        with patch("savva.agent.models._openrouter_model_price", return_value=(float("nan"), 0.0)):
            with self.assertRaises(ModelAdapterError):
                adapter.max_cost_estimate([], [], 10)


if __name__ == "__main__":
    unittest.main()
