from __future__ import annotations

import io
import json
import urllib.error
import unittest
from unittest.mock import patch

from savva.agent.failover import FailoverEngine
from savva.agent.models import ModelAdapter, ModelAdapterError, ModelResponse, ModelUsage, OpenRouterAdapter


class FallbackProbe(ModelAdapter):
    provider, model = "openrouter", "fallback"

    def __init__(self):
        self.calls = 0

    def max_cost_estimate(self, messages, tools, max_tokens):
        return 0.01

    def complete(self, messages, tools, *, max_tokens, timeout_seconds, tool_choice="auto"):
        self.calls += 1
        return ModelResponse("fallback", [], ModelUsage(), "stop")


class PrimaryAdapter(ModelAdapter):
    provider, model = "openrouter", "primary"

    def __init__(self, adapter):
        self.adapter = adapter

    def read_rate_limits(self):
        return {"rateLimits": {"primary": {"usedPercent": 10}}}

    def complete(self, messages, tools, *, max_tokens, timeout_seconds, tool_choice="auto"):
        return self.adapter.complete(messages, tools, max_tokens=max_tokens,
                                     timeout_seconds=timeout_seconds, tool_choice=tool_choice)


class ProviderFailureTests(unittest.TestCase):
    def _failure(self, status, payload):
        body = json.dumps(payload).encode()
        return urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions",
                                      status, "test", {}, io.BytesIO(body))

    def test_auth_rate_limit_overload_and_timeout_do_not_trigger_quota_failover(self):
        cases = [
            (401, {"error": {"code": "unauthorized", "type": "authentication_error"}}, "auth"),
            (403, {"error": {"code": "forbidden", "type": "authentication_error"}}, "auth"),
            (429, {"error": {"code": "rate_limited", "type": "rate_limit_error"}}, "rate_limit"),
            (503, {"error": {"code": "busy", "type": "server_error"}}, "overload"),
        ]
        for status, payload, expected_kind in cases:
            with self.subTest(status=status):
                adapter = OpenRouterAdapter(model="test/model", base_url="https://openrouter.ai/api/v1",
                                            api_key="test-placeholder")
                fallback = FallbackProbe()
                with patch("urllib.request.urlopen", side_effect=self._failure(status, payload)):
                    with self.assertRaises(ModelAdapterError) as raised:
                        FailoverEngine(PrimaryAdapter(adapter), fallback).complete(
                            [], [], max_tokens=64, timeout_seconds=1)
                self.assertEqual(raised.exception.failure_kind, expected_kind)
                self.assertEqual(fallback.calls, 0)

        adapter = OpenRouterAdapter(model="test/model", base_url="https://openrouter.ai/api/v1",
                                    api_key="test-placeholder")
        fallback = FallbackProbe()
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            with self.assertRaises(ModelAdapterError):
                FailoverEngine(PrimaryAdapter(adapter), fallback).complete(
                    [], [], max_tokens=64, timeout_seconds=1)
        self.assertEqual(fallback.calls, 0)


if __name__ == "__main__":
    unittest.main()
