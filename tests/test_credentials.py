from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from savva.agent.credentials import WindowsDPAPISecretStore


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
class WindowsDPAPISecretStoreTests(unittest.TestCase):
    def test_protected_key_survives_store_recreation_without_plaintext_file(self) -> None:
        sentinel = "sk-or-test-dpapi-roundtrip-only"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "openrouter-key.dpapi"
            WindowsDPAPISecretStore(path).set(sentinel)
            encrypted = path.read_bytes()
            self.assertNotIn(sentinel.encode("utf-8"), encrypted)
            self.assertEqual(WindowsDPAPISecretStore(path).get(), sentinel)
            WindowsDPAPISecretStore(path).delete()
            self.assertIsNone(WindowsDPAPISecretStore(path).get())


if __name__ == "__main__":
    unittest.main()
