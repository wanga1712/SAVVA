from __future__ import annotations

import unittest

from savva.agent.openrouter import OpenRouterAccountClient, OpenRouterError


class FakeTransport:
    def __init__(self, response: dict[str, object]):
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, url: str, headers: dict[str, str], timeout_seconds: float):
        self.calls.append((url, headers))
        return self.response


class OpenRouterDiscoveryTests(unittest.TestCase):
    def test_key_status_returns_only_allowlisted_account_fields(self) -> None:
        transport = FakeTransport({"data": {
            "label": "safe-label", "limit": 1, "limit_remaining": 0.75,
            "usage": 0.25, "limit_reset": "monthly", "is_management_key": False,
            "secret": "must-not-be-returned",
        }})
        status = OpenRouterAccountClient(transport).key_status("local-test-key")
        self.assertEqual(status["limit_remaining"], 0.75)
        self.assertNotIn("secret", status)
        self.assertNotIn("label", status)
        self.assertNotIn("local-test-key", repr(status))
        self.assertEqual(transport.calls[0][0], "https://openrouter.ai/api/v1/key")
        self.assertEqual(transport.calls[0][1]["Authorization"], "Bearer local-test-key")

    def test_catalog_marks_candidates_catalog_only_and_extracts_capabilities(self) -> None:
        transport = FakeTransport({"data": [
            {
                "id": "qwen/qwen3-coder-next", "name": "Qwen Coder Next",
                "context_length": 65536,
                "architecture": {"input_modalities": ["text", "image"]},
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "supported_parameters": ["tools", "response_format"],
            },
            {
                "id": "vendor/cheap-text", "name": "Cheap Text",
                "context_length": 8192,
                "architecture": {"input_modalities": ["text"]},
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": [],
            },
        ]})
        result = OpenRouterAccountClient(transport).discover_models("local-test-key")
        self.assertEqual(result["catalog_count"], 2)
        coder = result["models"][0]
        self.assertEqual(coder["model_id"], "qwen/qwen3-coder-next")
        self.assertEqual(coder["availability_status"], "CATALOG_ONLY")
        self.assertTrue(coder["tool_calling"])
        self.assertTrue(coder["structured_output"])
        self.assertTrue(coder["image_input"])
        self.assertEqual(result["models"][2]["model_id"], "vendor/cheap-text")
        self.assertTrue(result["models"][2]["catalog_candidate"])

    def test_missing_catalog_candidate_is_unavailable_not_live_verified(self) -> None:
        result = OpenRouterAccountClient(FakeTransport({"data": []})).discover_models("local-test-key")
        self.assertEqual(result["models"][0]["availability_status"], "UNAVAILABLE")

    def test_exhausted_key_limit_is_reported_as_zero_without_inference(self) -> None:
        client = OpenRouterAccountClient(FakeTransport({"data": {
            "limit": 1, "limit_remaining": 0, "usage": 1,
            "is_management_key": False,
        }}))
        status = client.key_status("local-test-key")
        self.assertEqual(status["limit_remaining"], 0)

    def test_provider_network_failure_is_classified_without_secret(self) -> None:
        class OfflineTransport:
            def get_json(self, *_args):
                raise OpenRouterError("OPENROUTER_UNAVAILABLE", "OpenRouter could not be reached.")

        with self.assertRaises(OpenRouterError) as raised:
            OpenRouterAccountClient(OfflineTransport()).discover_models("local-test-key")
        self.assertEqual(raised.exception.code, "OPENROUTER_UNAVAILABLE")
        self.assertNotIn("local-test-key", raised.exception.safe_message)

    def test_authorization_error_is_safe_and_classified(self) -> None:
        class UnauthorizedTransport:
            def get_json(self, *_args):
                raise OpenRouterError("AUTH_FAILED", "OpenRouter rejected this API key.")

        with self.assertRaises(OpenRouterError) as raised:
            OpenRouterAccountClient(UnauthorizedTransport()).key_status("local-test-key")
        self.assertEqual(raised.exception.code, "AUTH_FAILED")
        self.assertNotIn("local-test-key", raised.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
