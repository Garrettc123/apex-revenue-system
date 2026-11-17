"""Hermetic compatibility tests for Termux SQLite discovery and safe diagnostics."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import sync_to_master_hub as sync


class TestTermuxSyncEntrypoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / sync.DB_FILENAME

    def seed(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE task_queue (status TEXT)")
            connection.execute("INSERT INTO task_queue VALUES ('pending')")
            connection.execute(
                "CREATE TABLE event_ledger "
                "(event_id TEXT, event_type TEXT, status TEXT, timestamp TEXT)"
            )
            connection.execute(
                "INSERT INTO event_ledger VALUES "
                "('private-event', 'private-message', 'completed', '2026-11-17')"
            )
            connection.execute("CREATE TABLE dead_letter_quarantine (id INTEGER)")

    def test_db_auto_discovery(self):
        self.seed()
        self.assertEqual(
            sync.resolve_database(Path("auto"), roots=[self.base]),
            self.db.resolve(),
        )

    def test_no_database_is_not_silent_success(self):
        with self.assertRaises(sync.SyncError):
            sync.resolve_database(Path("auto"), roots=[self.base])
        self.assertFalse(self.db.exists())

    def test_diagnostic_requires_no_hub_clone_and_leaks_no_ids(self):
        self.seed()
        result = sync.snapshot(self.db)
        self.assertTrue(result["complete"])
        self.assertIn("pending", result["coverage"]["task_queue"]["status_counts"])
        self.assertNotIn("private-event", json.dumps(result))
        self.assertNotIn("private-message", json.dumps(result))
        self.assertEqual(sync.main(["--db", str(self.db), "--diagnose"]), 0)

    def test_apex_repo_refused_as_public_sync_target(self):
        self.seed()
        apex = self.base / "apex-revenue-system"
        apex.mkdir()
        (apex / ".git").mkdir()
        with patch.object(sync, "_git", return_value="https://github.com/Garrettc123/apex-revenue-system.git"):
            self.assertEqual(
                sync.main(["--db", str(self.db), "--repo", str(apex)]),
                1,
            )
        self.assertFalse((apex / sync.EXPORT_RELATIVE).exists())


if __name__ == "__main__":
    unittest.main()
