from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from savva.agent.network import TailscaleExitStatus
from savva.agent.orchestrator import AgentRuntimeError
from savva.server import AppRuntime, SavvaHTTPServer, SavvaRequestHandler


class FakeTailscaleInspector:
    def __init__(self, status: TailscaleExitStatus):
        self.status = status

    def inspect(self) -> TailscaleExitStatus:
        return self.status


class MemorySecretStore:
    def __init__(self) -> None:
        self.value: str | None = None

    def get(self) -> str | None:
        return self.value

    def set(self, value: str) -> None:
        self.value = value

    def delete(self) -> None:
        self.value = None


class FakeOpenRouterClient:
    def key_status(self, api_key: str) -> dict[str, object]:
        if api_key == "bad-test-key":
            from savva.agent.openrouter import OpenRouterError
            raise OpenRouterError("AUTH_FAILED", "OpenRouter rejected this API key.")
        return {
            "limit": 3, "limit_remaining": 2.75,
            "is_management_key": api_key == "management-test-key",
        }

    def discover_models(self, api_key: str) -> dict[str, object]:
        return {"catalog_count": 1, "models": [{"model_id": "example/catalog"}]}


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        environment = {
            "SAVVA_WORKSPACE_ROOT": str(root / "workspace"),
            "SAVVA_PROVIDER": "openrouter",
            "SAVVA_MODEL": "nvidia/nemotron-3.5-lightning:free",
            "SAVVA_BASE_URL": "https://openrouter.ai/api/v1",
        }
        self.environment_patch = patch.dict(os.environ, environment, clear=False)
        self.environment_patch.start()
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.server = SavvaHTTPServer(("127.0.0.1", 0), SavvaRequestHandler)
        self.tailscale = FakeTailscaleInspector(
            TailscaleExitStatus(
                installed=True,
                backend_state="Running",
                exit_node_id="node-1",
                exit_node_hostname="squealing-orange",
                exit_node_dns_name="squealing-orange.example.ts.net",
                exit_node_online=True,
                exit_node_addresses=("100.87.196.23",),
                ready=True,
            )
        )
        self.server.runtime = AppRuntime(
            project_root=root,
            settings_path=root / "settings.json",
            usage_path=root / "usage.jsonl",
            action_log_path=root / "actions.jsonl",
            tailscale_inspector=self.tailscale,
            secret_store=MemorySecretStore(),
            openrouter_client=FakeOpenRouterClient(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.environment_patch.stop()
        self.temporary.cleanup()

    def test_health_and_ui_do_not_expose_key(self) -> None:
        with urllib.request.urlopen(self.base_url + "/api/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
            csp = response.headers.get("Content-Security-Policy")
        with urllib.request.urlopen(self.base_url + "/", timeout=5) as response:
            html = response.read().decode("utf-8")

        self.assertFalse(health["api_key_configured"])
        self.assertEqual(health["tailscale"]["exit_node_hostname"], "squealing-orange")
        self.assertTrue(health["tailscale"]["ready"])
        self.assertNotIn("OPENROUTER_API_KEY=", json.dumps(health))
        self.assertIn("default-src 'self'", csp)
        self.assertIn("SAVVA Agent", html)

    def test_chat_without_backend_key_returns_safe_error(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(
                {"message": "Check Node.js", "session_id": "session-test-1"}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-SAVVA-Request": "1",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        error = json.loads(raised.exception.read().decode("utf-8"))

        self.assertEqual(error["error"]["code"], "API_KEY_MISSING")

    def test_openrouter_key_is_stored_without_echo_or_settings_persistence(self) -> None:
        secret = "sk-or-test-unique-secret-not-for-output"
        request = urllib.request.Request(
            self.base_url + "/api/openrouter/key",
            data=json.dumps({"api_key": secret}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-SAVVA-Request": "1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
        self.assertNotIn(secret, body)
        self.assertEqual(self.server.runtime.api_key(), secret)
        self.assertNotIn(secret, (Path(self.temporary.name) / "settings.json").read_text(encoding="utf-8") if (Path(self.temporary.name) / "settings.json").exists() else "")

        with urllib.request.urlopen(self.base_url + "/api/openrouter/key-status", timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        self.assertEqual(status["source"], "windows_dpapi")
        self.assertNotIn(secret, json.dumps(status))

    def test_openrouter_account_check_and_catalog_are_read_only(self) -> None:
        self.server.runtime.secret_store.set("good-test-key")
        with urllib.request.urlopen(self.base_url + "/api/openrouter/key-check", timeout=5) as response:
            account = json.loads(response.read().decode("utf-8"))
        self.assertEqual(account["key"]["limit_remaining"], 2.75)
        with urllib.request.urlopen(self.base_url + "/api/openrouter/models", timeout=5) as response:
            catalog = json.loads(response.read().decode("utf-8"))
        self.assertEqual(catalog["catalog_count"], 1)
        self.assertNotIn("good-test-key", json.dumps(account) + json.dumps(catalog))

    def test_management_key_is_rejected_and_bad_auth_is_classified(self) -> None:
        self.server.runtime.secret_store.set("management-test-key")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(self.base_url + "/api/openrouter/key-check", timeout=5)
        response = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual(response["error"]["code"], "MANAGEMENT_KEY_NOT_ALLOWED")

        self.server.runtime.secret_store.set("bad-test-key")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(self.base_url + "/api/openrouter/key-check", timeout=5)
        response = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual(raised.exception.code, 401)
        self.assertEqual(response["error"]["code"], "AUTH_FAILED")
        self.assertNotIn("bad-test-key", json.dumps(response))

    def test_openrouter_key_delete_requires_explicit_endpoint(self) -> None:
        self.server.runtime.secret_store.set("stored-test-key")
        request = urllib.request.Request(
            self.base_url + "/api/openrouter/key/delete", data=b"{}",
            headers={"Content-Type": "application/json", "X-SAVVA-Request": "1"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        self.assertFalse(status["key"]["configured"])

    def test_post_without_same_origin_marker_is_rejected(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/api/reset",
            data=b'{"session_id":"session-test-2"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        error = json.loads(raised.exception.read().decode("utf-8"))

        self.assertEqual(raised.exception.code, 403)
        self.assertEqual(error["error"]["code"], "ORIGIN_DENIED")

    def test_cross_origin_write_request_is_rejected(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/api/reset",
            data=b'{"session_id":"session-test-3"}',
            headers={
                "Content-Type": "application/json",
                "X-SAVVA-Request": "1",
                "Origin": "https://evil.example",
            },
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(raised.exception.code, 403)

    def test_proposal_confirmation_writes_exact_approved_files(self) -> None:
        service = self.server.runtime.project_service()
        session_id = "confirm-session-1"
        proposal = service.create_template_proposal(
            project_name="confirmed-store",
            title="SAVVA Computers",
            session_id=session_id,
        )
        confirmation = service.get_confirmation(proposal["proposal_id"], session_id)
        body = json.dumps(
            {
                "proposal_id": proposal["proposal_id"],
                "confirmation_token": confirmation["confirmation_token"],
                "session_id": session_id,
                "decision": "approve",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/confirm",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-SAVVA-Request": "1",
                "Origin": self.base_url,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertEqual(result["proposal"]["status"], "applied")
        self.assertTrue(
            (Path(self.temporary.name) / "workspace" / "confirmed-store" / "package.json").is_file()
        )

    def test_proposal_rejection_over_http_does_not_write(self) -> None:
        service = self.server.runtime.project_service()
        session_id = "reject-session-1"
        proposal = service.propose(
            summary="Reject this file",
            project_path="rejected-project",
            changes=[{"path": "index.html", "content": "<h1>Rejected</h1>"}],
            session_id=session_id,
        )
        confirmation = service.get_confirmation(proposal["proposal_id"], session_id)
        body = json.dumps(
            {
                "proposal_id": proposal["proposal_id"],
                "confirmation_token": confirmation["confirmation_token"],
                "session_id": session_id,
                "decision": "reject",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/confirm",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-SAVVA-Request": "1",
                "Origin": self.base_url,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertEqual(result["proposal"]["status"], "rejected")
        self.assertFalse(
            (Path(self.temporary.name) / "workspace" / "rejected-project").exists()
        )

    def test_expected_tailscale_exit_node_is_enforced(self) -> None:
        settings = self.server.runtime.settings()
        settings = type(settings)(
            **{
                **settings.to_public_dict(),
                "require_tailscale_exit_node": True,
                "expected_exit_node": "another-node",
            }
        )

        with self.assertRaises(AgentRuntimeError) as raised:
            AppRuntime._validate_egress(settings, self.tailscale.status)

        self.assertEqual(raised.exception.code, "TAILSCALE_EXIT_NODE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
