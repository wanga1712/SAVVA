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

from savva.server import AppRuntime, SavvaHTTPServer, SavvaRequestHandler


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
        self.server.runtime = AppRuntime(
            project_root=root,
            settings_path=root / "settings.json",
            usage_path=root / "usage.jsonl",
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
        self.assertNotIn("OPENROUTER_API_KEY=", json.dumps(health))
        self.assertIn("default-src 'self'", csp)
        self.assertIn("SAVVA Agent", html)

    def test_chat_without_backend_key_returns_safe_error(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(
                {"message": "Check Node.js", "session_id": "session-test-1"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        error = json.loads(raised.exception.read().decode("utf-8"))

        self.assertEqual(error["error"]["code"], "API_KEY_MISSING")


if __name__ == "__main__":
    unittest.main()
