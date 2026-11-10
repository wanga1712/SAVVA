from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from savva.agent.projects import ActionJournal, ProjectService
from savva.core.registry import ToolExecutionError


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        self.journal_path = Path(self.temporary.name) / "actions.jsonl"
        self.service = ProjectService(self.root, ActionJournal(self.journal_path))
        self.session_id = "project-session-1"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_template_proposal_does_not_write_until_confirmed(self) -> None:
        proposal = self.service.create_template_proposal(
            project_name="savva-store",
            title="SAVVA Computers",
            session_id=self.session_id,
        )
        project_dir = self.root / "savva-store"
        confirmation = self.service.get_confirmation(
            proposal["proposal_id"], self.session_id
        )
        validation = self.service.validate(
            proposal_id=proposal["proposal_id"],
            session_id=self.session_id,
        )

        self.assertFalse(project_dir.exists())
        self.assertIn("src/App.tsx", confirmation["diff"])
        self.assertTrue(validation["valid"])
        self.assertEqual(proposal["status"], "pending")

        applied = self.service.apply(
            proposal_id=proposal["proposal_id"],
            confirmation_token=confirmation["confirmation_token"],
            session_id=self.session_id,
        )

        self.assertEqual(applied["status"], "applied")
        self.assertTrue((project_dir / "package.json").is_file())
        self.assertTrue((project_dir / "src" / "App.tsx").is_file())
        self.assertIn(
            "SAVVA GAME",
            (project_dir / "src" / "data" / "computers.ts").read_text(encoding="utf-8"),
        )
        with self.assertRaises(ToolExecutionError) as raised:
            self.service.apply(
                proposal_id=proposal["proposal_id"],
                confirmation_token=confirmation["confirmation_token"],
                session_id=self.session_id,
            )
        self.assertEqual(raised.exception.code, "PROPOSAL_NOT_PENDING")

    def test_rejection_does_not_change_files(self) -> None:
        proposal = self.service.propose(
            summary="Create a file",
            project_path="demo",
            changes=[{"path": "index.html", "content": "<h1>Demo</h1>"}],
            session_id=self.session_id,
        )
        confirmation = self.service.get_confirmation(
            proposal["proposal_id"], self.session_id
        )

        rejected = self.service.reject(
            proposal_id=proposal["proposal_id"],
            confirmation_token=confirmation["confirmation_token"],
            session_id=self.session_id,
        )

        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse((self.root / "demo" / "index.html").exists())

    def test_existing_file_update_requires_hash_and_detects_race(self) -> None:
        target = self.root / "notes.json"
        target.write_text('{"version":1}', encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        proposal = self.service.propose(
            summary="Update notes",
            project_path=".",
            changes=[
                {
                    "path": "notes.json",
                    "content": '{"version":2}',
                    "expected_sha256": digest,
                }
            ],
            session_id=self.session_id,
        )
        confirmation = self.service.get_confirmation(
            proposal["proposal_id"], self.session_id
        )
        target.write_text('{"version":99}', encoding="utf-8")

        with self.assertRaises(ToolExecutionError) as raised:
            self.service.apply(
                proposal_id=proposal["proposal_id"],
                confirmation_token=confirmation["confirmation_token"],
                session_id=self.session_id,
            )

        self.assertEqual(raised.exception.code, "STALE_PROPOSAL")
        self.assertEqual(target.read_text(encoding="utf-8"), '{"version":99}')

    def test_existing_file_requires_expected_hash(self) -> None:
        (self.root / "existing.json").write_text("{}", encoding="utf-8")

        with self.assertRaises(ToolExecutionError) as raised:
            self.service.propose(
                summary="Unsafe update",
                project_path=".",
                changes=[{"path": "existing.json", "content": "{\"x\":1}"}],
                session_id=self.session_id,
            )

        self.assertEqual(raised.exception.code, "EXPECTED_HASH_REQUIRED")

    def test_path_traversal_and_sensitive_files_are_blocked(self) -> None:
        for path in ("../outside.json", "C:\\Windows\\file.json", ".env", ".git/config"):
            with self.subTest(path=path), self.assertRaises(ToolExecutionError):
                self.service.propose(
                    summary="Unsafe path",
                    project_path=".",
                    changes=[{"path": path, "content": "{}"}],
                    session_id=self.session_id,
                )

    def test_secret_material_is_blocked(self) -> None:
        with self.assertRaises(ToolExecutionError) as raised:
            self.service.propose(
                summary="Secret",
                project_path="demo",
                changes=[
                    {
                        "path": "config.ts",
                        "content": (
                            'const key = "sk-'
                            'or-v1-abcdefghijklmnopqrstuvwxyz";'
                        ),
                    }
                ],
                session_id=self.session_id,
            )

        self.assertEqual(raised.exception.code, "SECRET_MATERIAL_BLOCKED")

    def test_junction_escape_is_rejected_when_supported(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        link = self.root / "linked"
        try:
            result = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(link),
                    str(outside),
                ],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                raise OSError("junction creation failed")
        except OSError:
            self.skipTest("Creating a directory junction is not permitted.")

        try:
            with self.assertRaises(ToolExecutionError) as raised:
                self.service.propose(
                    summary="Escape",
                    project_path="linked",
                    changes=[{"path": "inside.json", "content": "{}"}],
                    session_id=self.session_id,
                )
        finally:
            link.rmdir()

        self.assertEqual(raised.exception.code, "FILES_PATH_DENIED")

    def test_journal_contains_metadata_without_file_content(self) -> None:
        self.service.propose(
            summary="Journal test",
            project_path="demo",
            changes=[{"path": "index.html", "content": "private-content-marker"}],
            session_id=self.session_id,
        )
        journal = self.journal_path.read_text(encoding="utf-8")

        self.assertIn("propose", journal)
        self.assertNotIn("private-content-marker", journal)
        self.assertFalse((self.root / "demo" / "index.html").exists())

    def test_project_validation_detects_structural_problems(self) -> None:
        proposal = self.service.propose(
            summary="Incomplete",
            project_path="bad-project",
            changes=[{"path": "index.html", "content": "<h1>Incomplete</h1>"}],
            session_id=self.session_id,
        )
        result = self.service.validate(
            proposal_id=proposal["proposal_id"],
            session_id=self.session_id,
        )

        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"][0]["passed"])


if __name__ == "__main__":
    unittest.main()
