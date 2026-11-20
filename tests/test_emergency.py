from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from savva.emergency import __main__ as emergency


class EmergencyCoreTests(unittest.TestCase):
    def test_doctor_returns_deterministic_offline_report(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), \
             patch.object(emergency, "_http", return_value={"status": "unavailable"}), \
             patch.object(emergency, "_codex_status", return_value={"cli": "missing", "quota": "unknown"}), \
             patch.object(emergency.shutil, "which", return_value=None), \
             patch("savva.agent.credentials.WindowsDPAPISecretStore.get", return_value=None), \
             patch.object(emergency, "_openrouter_key_check", side_effect=AssertionError("Live provider access in unit tests")):
            report = emergency.doctor()
        self.assertEqual(report["component"], "SAVVA Emergency Core")
        self.assertEqual(report["mode"], "deterministic")
        self.assertEqual(report["checks"]["backend"]["status"], "unavailable")
        self.assertFalse(report["checks"]["openrouter"]["key_configured"])
        self.assertEqual(report["checks"]["local_llm"]["runtime"], "missing")

    def test_openrouter_key_check_never_returns_authorization_header(self) -> None:
        with patch.object(emergency.urllib.request, "urlopen") as open_url:
            open_url.side_effect = __import__("urllib.error").error.HTTPError(
                "https://openrouter.ai/api/v1/key", 401, "unauthorized", {}, None
            )
            result = emergency._openrouter_key_check("synthetic-only-test-key", "test")
        self.assertEqual(result["status"], "auth_failed")
        self.assertNotIn("synthetic-only-test-key", str(result))
        self.assertNotIn("Authorization", str(result))


if __name__ == "__main__":
    unittest.main()
