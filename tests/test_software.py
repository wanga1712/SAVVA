from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from savva.tools.software import SoftwareDetector


class SoftwareDetectorTests(unittest.TestCase):
    def test_detects_installed_program_and_version(self) -> None:
        detector = SoftwareDetector()
        with (
            patch.object(
                detector, "_find_executable", return_value=r"C:\Tools\node.exe"
            ),
            patch.object(
                detector, "_run_version", return_value=("24.22.1", None)
            ),
        ):
            result = detector.detect("node")

        self.assertTrue(result["installed"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["version"], "24.22.1")
        self.assertEqual(result["source"], "PATH")

    def test_missing_program_is_not_an_execution_error(self) -> None:
        detector = SoftwareDetector()
        with patch.object(detector, "_find_executable", return_value=None):
            result = detector.detect("git")

        self.assertFalse(result["installed"])
        self.assertIsNone(result["version"])
        self.assertIsNone(result["probe_error"])

    def test_ollama_is_not_launched_during_discovery(self) -> None:
        detector = SoftwareDetector()
        with (
            patch.object(
                detector, "_find_executable", return_value=r"C:\Tools\ollama.exe"
            ),
            patch.object(detector, "_run_version") as run_version,
        ):
            result = detector.detect("ollama")

        self.assertTrue(result["installed"])
        self.assertIsNone(result["version"])
        run_version.assert_not_called()


if __name__ == "__main__":
    unittest.main()
