"""Hermetic QIL tests; never execute arbitrary SQL or access network services."""
from datetime import date, datetime, timezone
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import qil

DATE = date(2026, 11, 17)


def record(*, complete=True, queue=None, audit=None, quarantine=None):
    if queue is None:
        queue = {"present": "present", "status_counts": {"pending": 2, "failed": 1}}
    if audit is None:
        audit = {"state": "present", "latest_ten_status_counts": {"failed": 1}, "window_size": 1}
    if quarantine is None:
        quarantine = {"state": "present", "count": 2}
    if "state" not in queue:
        queue = {"state": "present", "status_counts": queue["status_counts"]}
    return {
        "schema": "garcar.public_swarm_aggregate.v1",
        "captured_utc_date": "2026-11-17",
        "integrity": "ok",
        "complete": complete,
        "coverage": {
            "task_queue": queue,
            "event_ledger": audit,
            "dead_letter_quarantine": quarantine,
        },
    }


class TestQILContract(unittest.TestCase):
    def test_query_catalog_and_no_general_sql(self):
        self.assertEqual(set(qil.QUERIES),
                         {"overview", "queue", "audit", "quarantine", "integrity", "priorities"})
        for text in ("SELECT * FROM event_ledger", "DROP TABLE task_queue", "ql",
                     "wallet_sign", "../keys", ""):
            with self.subTest(text=text), self.assertRaises(qil.QueryError):
                qil.answer(text, record(), current_utc_date=DATE)

    def test_overview_read_only_unverified_financial_claims(self):
        result = qil.answer("overview", record(), current_utc_date=DATE)
        self.assertEqual(result["answer_status"], "ready")
        self.assertEqual(result["query_id"], "overview")
        self.assertFalse(result["external_execution_enabled"])
        self.assertFalse(result["source_values_authenticated"])
        self.assertFalse(result["revenue_verified"])
        self.assertFalse(result["bounty_payout_verified"])
        self.assertFalse(result["data"]["business_health_verified"])

    def test_queue_aggregate_only(self):
        result = qil.answer("queue", record(), current_utc_date=DATE)
        self.assertEqual(result["data"]["total_tasks"], 3)
        self.assertEqual(result["data"]["status_counts"], {"failed": 1, "pending": 2})
        self.assertNotIn("event_id", json.dumps(result))

    def test_audit_window_does_not_claim_full_history(self):
        result = qil.answer("audit", record(), current_utc_date=DATE)
        self.assertEqual(result["data"]["recent_status_counts"], {"failed": 1})
        self.assertFalse(result["data"]["is_full_event_history"])

    def test_quarantine_count_uses_nullable_missing_semantics(self):
        result = qil.answer("quarantine", record(), current_utc_date=DATE)
        self.assertEqual(result["data"]["count"], 2)
        missing = record(quarantine={"state": "missing"})
        result = qil.answer("quarantine", missing, current_utc_date=DATE)
        self.assertEqual(result["answer_status"], "blocked")
        self.assertIsNone(result["data"]["count"])

    def test_partial_source_never_upgraded_to_ready(self):
        data = record(complete=False)
        for question in qil.QUERIES:
            with self.subTest(question=question):
                result = qil.answer(question, data, current_utc_date=DATE)
                self.assertEqual(result["answer_status"], "blocked")

    def test_stale_date_and_failed_integrity_block(self):
        data = record()
        data["captured_utc_date"] = "2026-11-10"
        data["integrity"] = "failed"
        result = qil.answer("queue", data, current_utc_date=DATE)
        self.assertEqual(result["answer_status"], "blocked")
        self.assertIn("snapshot_not_current_utc_date", result["limitations"])
        self.assertIn("sqlite_integrity_not_verified", result["limitations"])

    def test_nonstandard_status_cannot_leak_private_string(self):
        data = record(queue={"state": "present", "status_counts": {"person@example.com": 5}})
        result = qil.answer("queue", data, current_utc_date=DATE)
        self.assertEqual(result["answer_status"], "blocked")
        self.assertIsNone(result["data"]["status_counts"])
        self.assertNotIn("person@example.com", json.dumps(result))

    def test_malformed_counts_cannot_appear_as_verified(self):
        for counts in ({"failed": -1}, {"pending": True}, {"pending": 0.5}, []):
            with self.subTest(counts=counts):
                data = record(queue={"state": "present", "status_counts": counts})
                value = qil.answer("queue", data, current_utc_date=DATE)
                self.assertEqual(value["answer_status"], "blocked")

    def test_audit_window_inconsistency_blocks(self):
        data = record(audit={"state": "present", "latest_ten_status_counts": {"failed": 2}, "window_size": 1})
        value = qil.answer("audit", data, current_utc_date=DATE)
        self.assertEqual(value["answer_status"], "blocked")
        self.assertIsNone(value["data"]["window_size"])

    def test_priorities_only_advisory(self):
        result = qil.answer("priorities", record(), current_utc_date=DATE)
        self.assertEqual(result["data"]["advisory_actions"], [
            "inspect_dead_letter_quarantine_privately",
            "review_failed_or_blocked_tasks_privately",
            "review_pending_queue_and_authorization",
        ])
        self.assertFalse(result["data"]["agent_dispatch_triggered"])
        self.assertFalse(result["data"]["security_testing_authorized"])

    def test_unknown_or_missing_resources_block_priorities(self):
        data = record(quarantine={"state": "missing"})
        result = qil.answer("priorities", data, current_utc_date=DATE)
        self.assertEqual(result["answer_status"], "blocked")
        self.assertEqual(result["data"]["advisory_actions"],
                         ["reconcile_missing_or_stale_evidence"])

    def test_malformed_or_nonmapping_data_rejected(self):
        for value in (None, [], "sqlite"):
            with self.subTest(value=value), self.assertRaises(qil.QueryError):
                qil.answer("overview", value, current_utc_date=DATE)

    def test_snapshot_only_uses_allowlisted_source_fields(self):
        data = record()
        data["sensitive_private_event"] = "unsafe-secret"
        result = qil.answer("overview", data, current_utc_date=DATE)
        self.assertNotIn("unsafe-secret", json.dumps(result))

    def test_cli_list_does_not_discover_db_or_write(self):
        with patch.object(qil, "resolve_database", side_effect=AssertionError("must not access DB")):
            self.assertEqual(qil.main(["--list-queries"]), 0)

    def test_live_sqlite_fixture_only_returns_redacted_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "garcar_forge_operational.db"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE task_queue (status TEXT)")
                db.execute("INSERT INTO task_queue VALUES ('queued')")
                db.execute("CREATE TABLE event_ledger (event_id TEXT, event_type TEXT, status TEXT, timestamp TEXT)")
                db.execute("INSERT INTO event_ledger VALUES ('secret-key','sensitive','completed','2026-11-17T00:00:00Z')")
                db.execute("CREATE TABLE dead_letter_quarantine (id INTEGER)")
            with patch.object(qil, "resolve_database", return_value=source):
                self.assertEqual(qil.main(["--db", "auto", "--query", "queue"]), 0)
            result = qil.answer("queue", qil.snapshot(source, today=datetime(2026, 11, 17, tzinfo=timezone.utc)),
                                current_utc_date=DATE)
            self.assertNotIn("secret-key", json.dumps(result))
            self.assertEqual(result["data"]["status_counts"], {"queued": 1})


if __name__ == "__main__":
    unittest.main()
