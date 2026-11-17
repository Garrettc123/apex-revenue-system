"""QIL v2 regression tests using synthetic snapshots, not operational data."""
import copy
import unittest
from datetime import date, datetime, timezone

from scripts import qil

AS_OF = date(2026, 11, 17)


def fixture():
    return {
        "schema": qil.SCHEMA, "integrity": "ok", "complete": True,
        "captured_utc_date": AS_OF.isoformat(),
        "coverage": {
            "task_queue": {"state": "present", "status_counts": {"pending": 1}},
            "event_ledger": {"state": "present", "latest_ten_status_counts": {"completed": 1}, "window_size": 1},
            "dead_letter_quarantine": {"state": "present", "count": 0},
        },
    }


class QILHardeningV2(unittest.TestCase):
    def test_valid_all_queries(self):
        for query in qil.QUERIES:
            with self.subTest(query=query):
                result = qil.answer(query, fixture(), current_utc_date=AS_OF)
                self.assertEqual(result["answer_status"], "ready")
                self.assertTrue(result["source_complete"])
                for flag in ("external_execution_enabled", "source_values_authenticated", "revenue_verified", "bounty_payout_verified"):
                    self.assertIs(result[flag], False)

    def test_invalid_evidence_blocks_all_queries(self):
        variants = []
        for stamp in (None, "20261117", "2026-11-16", "2026-11-18", "invalid", False):
            r = fixture()
            r["captured_utc_date"] = stamp
            variants.append(r)
        for table, key, values in (
            ("task_queue", "status_counts", (None, {"pending": True}, {"invalid": 1}, {"failed": -1})),
            ("event_ledger", "window_size", (True, 11, 2)),
            ("dead_letter_quarantine", "count", (True, -1, "0")),
        ):
            for value in values:
                r = fixture()
                r["coverage"][table][key] = value
                variants.append(r)
        for i, record in enumerate(variants):
            for query in qil.QUERIES:
                with self.subTest(case=i, query=query):
                    result = qil.answer(query, copy.deepcopy(record), current_utc_date=AS_OF)
                    self.assertEqual(result["answer_status"], "blocked")
                    self.assertFalse(result["source_complete"])
                    self.assertTrue(result["limitations"])
                    if query == "priorities":
                        self.assertEqual(result["data"]["advisory_actions"], ["reconcile_missing_or_stale_evidence"])

    def test_invalid_as_of_dates_rejected(self):
        for value in (False, 0, "2026-11-17", datetime(2026, 11, 17, tzinfo=timezone.utc)):
            with self.subTest(value=value):
                with self.assertRaises(qil.QueryError):
                    qil.answer("overview", fixture(), current_utc_date=value)

    def test_input_unchanged(self):
        record = fixture()
        before = copy.deepcopy(record)
        for query in qil.QUERIES:
            qil.answer(query, record, current_utc_date=AS_OF)
        self.assertEqual(record, before)


if __name__ == "__main__":
    unittest.main()
