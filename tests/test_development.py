from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from savva.core.registry import ToolExecutionError
from savva.tools.development import DevelopmentInspector
from savva.tools.software import SOFTWARE_SPECS


class FakeSoftwareDetector:
    def __init__(self, installed: set[str]):
        self.installed = installed

    def detect(self, name):
        return {
            "name": name,
            "installed": name in self.installed,
            "version": "1.0.0" if name in self.installed else None,
            "verified": name in self.installed,
            "executable": name if name in self.installed else None,
            "source": "test",
            "probe_error": None,
        }


class DevelopmentInspectorTests(unittest.TestCase):
    def test_project_dependencies_are_split_into_declared_and_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "package.json").write_text(
                '{"dependencies":{"react":"^19.0.0"},"devDependencies":'
                '{"typescript":"^5.7.0"}}',
                encoding="utf-8",
            )
            (project / "requirements.txt").write_text(
                "requests==2.32.0\nmissing-savva-test>=1\n", encoding="utf-8"
            )
            typescript_package = project / "node_modules" / "typescript"
            typescript_package.mkdir(parents=True)
            (typescript_package / "package.json").write_text(
                '{"version":"5.7.2"}', encoding="utf-8"
            )

            detector = FakeSoftwareDetector(
                {"python", "pip", "node", "npm", "git", "npx"}
            )
            inspector = DevelopmentInspector(detector)
            with patch.object(
                inspector,
                "_installed_python_version",
                side_effect=lambda name: (
                    "2.32.0" if name == "requests" else None
                ),
            ):
                result = inspector.inspect(str(project))

        declared = result["project"]["declared_dependencies"]
        installed = result["project"]["installed_dependencies"]
        missing = result["project"]["missing_dependencies"]
        self.assertEqual(len(declared), 4)
        self.assertEqual({item["name"] for item in installed}, {"requests", "typescript"})
        self.assertEqual({item["name"] for item in missing}, {"react", "missing-savva-test"})

    def test_readiness_reports_missing_node(self) -> None:
        detector = FakeSoftwareDetector({"python", "pip", "git"})
        result = DevelopmentInspector(detector).inspect()

        self.assertEqual(result["readiness"]["status"], "needs_setup")
        self.assertEqual(result["readiness"]["missing_required"], ["node", "npm"])

    def test_project_path_must_be_an_existing_directory(self) -> None:
        inspector = DevelopmentInspector(FakeSoftwareDetector(set(SOFTWARE_SPECS)))
        with self.assertRaises(ToolExecutionError) as raised:
            inspector.inspect("C:\\definitely-missing-savva-project")

        self.assertEqual(raised.exception.code, "PROJECT_PATH_INVALID")


if __name__ == "__main__":
    unittest.main()
