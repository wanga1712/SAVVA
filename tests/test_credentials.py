from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from savva.agent.credentials import WindowsDPAPISecretStore


class DPAPISecretStoreTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
    def test_round_trip_encrypts_and_deletes_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "openrouter-key.dpapi"
            store = WindowsDPAPISecretStore(path)
            marker = "synthetic-test-secret-never-a-real-key"
            store.set(marker)
            encrypted = path.read_bytes()
            self.assertNotIn(marker.encode(), encrypted)
            self.assertEqual(store.get(), marker)
            store.delete()
            self.assertIsNone(store.get())


if __name__ == "__main__":
    unittest.main()
