from __future__ import annotations

import unittest

from savva.agent.failover import FailoverEngine, FailoverState, QuotaAvailability, QuotaSnapshot
from savva.agent.models import ModelAdapter, ModelAdapterError, ModelResponse, ModelUsage
from savva.agent.usage import BudgetExceeded


class FakeAdapter(ModelAdapter):
    def __init__(self, provider, model, *, limits=None, response=None, error=None, estimate=0.02):
        self.provider, self.model = provider, model
        self.limits, self.response, self.error = limits, response, error
        self.calls, self.messages = 0, []
        self.estimate = estimate

    def max_cost_estimate(self, messages, tools, max_tokens):
        return self.estimate

    def read_rate_limits(self):
        if callable(self.limits):
            return self.limits()
        return self.limits or {}

    def complete(self, messages, tools, *, max_tokens, timeout_seconds, tool_choice="auto"):
        self.calls += 1
        self.messages.append(messages)
        if self.error:
            raise self.error
        return self.response or ModelResponse("ok", [], ModelUsage(10, 5, 15, 0.01, True), "stop")


def limits(primary=10, secondary=20, reached=None, reset=None):
    return {"rateLimits": {"primary": {"usedPercent": primary, "resetsAt": reset},
                           "secondary": {"usedPercent": secondary, "resetsAt": reset},
                           "rateLimitReachedType": reached}}


class FailoverTests(unittest.TestCase):
    def test_normal_primary_route(self):
        primary, fallback = FakeAdapter("codex_chatgpt", "gpt", limits=limits()), FakeAdapter("openrouter", "backup")
        result = FailoverEngine(primary, fallback).complete([], [], max_tokens=32, timeout_seconds=1)
        self.assertEqual(result.content, "ok")
        self.assertEqual((primary.calls, fallback.calls), (1, 0))

    def test_confirmed_quota_switches_and_preserves_context(self):
        primary = FakeAdapter("codex_chatgpt", "gpt", limits=limits(100, 100))
        fallback = FakeAdapter("openrouter", "backup")
        messages = [{"role": "user", "content": "continue WIP-17"}]
        engine = FailoverEngine(primary, fallback)
        engine.complete(messages, [], max_tokens=32, timeout_seconds=1)
        self.assertEqual(engine.state, FailoverState.FALLBACK_ACTIVE)
        self.assertEqual(fallback.messages[0], messages)

    def test_quota_recovery_returns_on_next_boundary(self):
        samples = iter([limits(100, 100, reset=1), limits(10, 20)])
        primary = FakeAdapter("codex_chatgpt", "gpt", limits=lambda: next(samples))
        fallback = FakeAdapter("openrouter", "backup")
        engine = FailoverEngine(primary, fallback)
        context = [{"role": "user", "content": "continue WIP-17"}]
        first = engine.complete(context, [], max_tokens=32, timeout_seconds=1)
        engine.next_quota_check = 0
        resumed_context = context + [{"role": "assistant", "content": first.content}]
        engine.complete(resumed_context, [], max_tokens=32, timeout_seconds=1)
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(engine.state, FailoverState.PRIMARY_RECOVERED)
        self.assertEqual(primary.messages[0], resumed_context)

    def test_auth_timeout_and_overload_do_not_switch(self):
        for kind in ("auth", "transient"):
            with self.subTest(kind=kind):
                primary = FakeAdapter("codex_chatgpt", "gpt", limits=limits(),
                                      error=ModelAdapterError("NO", "safe", failure_kind=kind))
                fallback = FakeAdapter("openrouter", "backup")
                with self.assertRaises(ModelAdapterError):
                    FailoverEngine(primary, fallback).complete([], [], max_tokens=32, timeout_seconds=1)
                self.assertEqual(fallback.calls, 0)

    def test_fallback_budget_blocks_next_paid_call(self):
        primary = FakeAdapter("codex_chatgpt", "gpt", limits=limits(100, 100))
        fallback = FakeAdapter("openrouter", "backup", estimate=0.05)
        engine = FailoverEngine(primary, fallback, session_cost_usd=0.98,
                                task_budget_usd=1.0, max_request_cost_usd=0.05)
        with self.assertRaises(BudgetExceeded):
            engine.complete([], [], max_tokens=32, timeout_seconds=1)
        self.assertEqual(fallback.calls, 0)
        self.assertEqual(engine.state, FailoverState.BLOCKED)

    def test_quota_parser_reads_each_limit_bucket(self):
        snapshot = QuotaSnapshot.parse({"rateLimits": limits()["rateLimits"],
                                        "rateLimitsByLimitId": {"weekly": limits(30, 100)["rateLimits"]}})
        self.assertTrue(snapshot.exhausted)
        self.assertEqual(len(snapshot.windows), 4)

    def test_incomplete_quota_status_is_unknown_and_does_not_recover_fallback(self):
        samples = iter([limits(100, 100), {"rateLimits": {"primary": None}}])
        primary = FakeAdapter("codex_chatgpt", "gpt", limits=lambda: next(samples))
        fallback = FakeAdapter("openrouter", "backup")
        engine = FailoverEngine(primary, fallback)
        engine.complete([], [], max_tokens=32, timeout_seconds=1)
        engine.next_quota_check = 0
        engine.complete([], [], max_tokens=32, timeout_seconds=1)
        self.assertEqual(engine.state, FailoverState.FALLBACK_ACTIVE)
        self.assertEqual((primary.calls, fallback.calls), (0, 2))
        self.assertGreater(engine.next_quota_check, 0)

    def test_longest_exhausted_window_controls_next_probe(self):
        now = __import__("time").time()
        snapshot = QuotaSnapshot.parse({"rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 100, "resetsAt": now + 100},
                      "secondary": {"usedPercent": 100, "resetsAt": now + 1000}},
            "tools": {"primary": {"usedPercent": 10, "resetsAt": now + 50}},
        }})
        self.assertEqual(snapshot.availability, QuotaAvailability.EXHAUSTED)
        self.assertAlmostEqual(snapshot.next_check_at, now + 1000, delta=2)

    def test_unknown_pricing_blocks_paid_fallback_without_call(self):
        primary = FakeAdapter("codex_chatgpt", "gpt", limits=limits(100, 100))
        fallback = FakeAdapter("openrouter", "backup")
        fallback.max_cost_estimate = lambda messages, tools, tokens: (_ for _ in ()).throw(
            ModelAdapterError("PRICE", "missing", failure_kind="protocol"))
        with self.assertRaises(BudgetExceeded):
            FailoverEngine(primary, fallback).complete([], [{"function": {"name": "x"}}], max_tokens=32, timeout_seconds=1)
        self.assertEqual(fallback.calls, 0)

    def test_response_cost_without_provider_confirmation_keeps_reserve(self):
        primary = FakeAdapter("codex_chatgpt", "gpt", limits=limits(100, 100))
        fallback = FakeAdapter("openrouter", "backup", response=ModelResponse(
            "ok", [], ModelUsage(10, 5, 15, 0.0, False), "stop"), estimate=0.03)
        engine = FailoverEngine(primary, fallback)
        response = engine.complete([], [], max_tokens=32, timeout_seconds=1)
        self.assertEqual(response.usage.cost_usd, 0.03)
        self.assertTrue(response.usage.cost_estimated)
        self.assertEqual(engine.session_cost_usd, 0.03)

    def test_post_call_cost_over_request_cap_blocks_further_calls(self):
        primary = FakeAdapter("codex_chatgpt", "gpt", limits=limits(100, 100))
        fallback = FakeAdapter("openrouter", "backup", estimate=0.02,
                               response=ModelResponse("ok", [], ModelUsage(10, 5, 15, 0.06, True), "stop"))
        engine = FailoverEngine(primary, fallback, max_request_cost_usd=0.05)
        with self.assertRaises(BudgetExceeded):
            engine.complete([], [], max_tokens=32, timeout_seconds=1)
        self.assertEqual(engine.state, FailoverState.BLOCKED)
        self.assertAlmostEqual(engine.session_cost_usd, 0.06)


if __name__ == "__main__":
    unittest.main()
