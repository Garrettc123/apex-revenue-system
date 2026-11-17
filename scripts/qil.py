#!/usr/bin/env python3
"""Garcar Query Intelligence Layer (QIL): offline, aggregate-only diagnostics.

Use only named questions. Never execute user-provided SQL, external tools,
wallet transactions, network requests, or write to the SQLite database.
The QIL classifies snapshot completeness separately from business outcomes.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__:
    from .sync_to_master_hub import resolve_database, snapshot, SyncError, SCHEMA
else:
    from sync_to_master_hub import resolve_database, snapshot, SyncError, SCHEMA

QIL_VERSION = "garcar.qil.v1"
QUERIES = ("overview", "queue", "audit", "quarantine", "integrity", "priorities")
REQUIRED_TABLES = ("task_queue", "event_ledger", "dead_letter_quarantine")
UNVERIFIED = "unverified"


class QueryError(ValueError):
    """Invalid question or source payload; never run ad-hoc SQL."""


def _count_map(value: object) -> dict[str, int] | None:
    """Require canonical aggregate fields; never echo arbitrary values."""
    if not isinstance(value, dict):
        return None
    allowed = frozenset({
        "pending", "queued", "running", "in_progress", "retrying", "paused",
        "blocked", "succeeded", "completed", "failed", "cancelled", "other",
    })
    if (any(not isinstance(k, str) or k not in allowed for k in value)
            or any(type(v) is not int or v < 0 for v in value.values())):
        return None
    return dict(sorted(value.items()))


def _table_coverage(record: Mapping[str, Any], table: str) -> str:
    tables = record.get("coverage")
    if not isinstance(tables, dict):
        return "invalid"
    item = tables.get(table)
    if not isinstance(item, dict):
        return "missing"
    kind = item.get("state")
    if kind not in ("present", "missing", "schema_mismatch"):
        return "invalid"
    return kind


def _read_status(record: Mapping[str, Any], now: date) -> tuple[str, list[str]]:
    """A complete report is an observed DB check, not a healthy business."""
    issues: list[str] = []
    if record.get("schema") != SCHEMA:
        issues.append("unexpected_snapshot_schema")
    if record.get("integrity") != "ok":
        issues.append("sqlite_integrity_not_verified")
    if record.get("complete") is not True:
        issues.append("source_snapshot_incomplete")
    stamp = record.get("captured_utc_date")
    try:
        if not isinstance(stamp, str) or date.fromisoformat(stamp) != now:
            issues.append("snapshot_not_current_utc_date")
    except ValueError:
        issues.append("snapshot_date_invalid")
    for table in REQUIRED_TABLES:
        state = _table_coverage(record, table)
        if state != "present":
            issues.append(f"table_{table}_{state}")
    return ("ready" if not issues else "blocked", issues)


def answer(question: str, record: Mapping[str, Any], *,
           current_utc_date: date | None = None) -> dict[str, Any]:
    """Pure interpreter over the allowlisted export; no SQL or tool calls."""
    if type(question) is not str or question not in QUERIES:
        raise QueryError("question not in allowed QIL catalog")
    if not isinstance(record, dict):
        raise QueryError("invalid snapshot object")
    current = current_utc_date or datetime.now(timezone.utc).date()
    if not isinstance(current, date):
        raise QueryError("invalid as-of date")
    state, issues = _read_status(record, current)
    coverage = {name: _table_coverage(record, name) for name in REQUIRED_TABLES}
    tables = record.get("coverage") if isinstance(record.get("coverage"), dict) else {}
    response: dict[str, Any] = {
        "qil_version": QIL_VERSION,
        "query_id": question,
        "source_kind": "local_sqlite_aggregate_snapshot",
        "source_complete": state == "ready",
        "answer_status": state,
        "limitations": sorted(set(issues)),
        "external_execution_enabled": False,
        "source_values_authenticated": False,
        "revenue_verified": False,
        "bounty_payout_verified": False,
        "data": {},
    }

    if question == "integrity":
        response["data"] = {
            "sqlite_quick_check": "ok" if record.get("integrity") == "ok" else "unverified",
            "schema_matches": record.get("schema") == SCHEMA,
            "current_date": isinstance(record.get("captured_utc_date"), str)
            and record.get("captured_utc_date") == current.isoformat(),
            "table_coverage": coverage,
        }
    elif question == "queue":
        queue = tables.get("task_queue", {}) if coverage["task_queue"] == "present" else {}
        counts = _count_map(queue.get("status_counts")) if isinstance(queue, dict) else None
        if counts is None:
            response["answer_status"] = "blocked"
            response["limitations"].append("queue_counts_unavailable")
        response["data"] = {
            "status_counts": counts,
            "total_tasks": sum(counts.values()) if counts is not None else None,
        }
    elif question == "audit":
        audit = tables.get("event_ledger", {}) if coverage["event_ledger"] == "present" else {}
        counts = _count_map(audit.get("latest_ten_status_counts")) if isinstance(audit, dict) else None
        window = audit.get("window_size") if isinstance(audit, dict) else None
        if (counts is None or type(window) is not int
                or not 0 <= window <= 10
                or sum(counts.values()) != window):
            response["answer_status"] = "blocked"
            response["limitations"].append("audit_window_unavailable_or_inconsistent")
            counts, window = None, None
        response["data"] = {
            "recent_status_counts": counts, "window_size": window,
            "is_full_event_history": False,
        }
    elif question == "quarantine":
        quarantine = tables.get("dead_letter_quarantine", {}) if coverage["dead_letter_quarantine"] == "present" else {}
        count = quarantine.get("count") if isinstance(quarantine, dict) else None
        if type(count) is not int or count < 0:
            response["answer_status"] = "blocked"
            response["limitations"].append("quarantine_count_unavailable")
            count = None
        response["data"] = {"count": count, "absence_means_clean": False}
    elif question == "priorities":
        queue = tables.get("task_queue", {}) if coverage["task_queue"] == "present" else {}
        counts = _count_map(queue.get("status_counts")) if isinstance(queue, dict) else None
        quarantine = tables.get("dead_letter_quarantine", {}) if coverage["dead_letter_quarantine"] == "present" else {}
        dead = quarantine.get("count") if isinstance(quarantine, dict) else None
        tasks = []
        if state != "ready" or counts is None or type(dead) is not int or dead < 0:
            response["answer_status"] = "blocked"
            tasks.append("reconcile_missing_or_stale_evidence")
        else:
            if dead > 0:
                tasks.append("inspect_dead_letter_quarantine_privately")
            if counts.get("failed", 0) > 0 or counts.get("blocked", 0) > 0:
                tasks.append("review_failed_or_blocked_tasks_privately")
            if counts.get("pending", 0) > 0 or counts.get("queued", 0) > 0:
                tasks.append("review_pending_queue_and_authorization")
            if not tasks:
                tasks.append("continue_monitoring_no_issues_in_selected_aggregates")
        response["data"] = {
            "advisory_actions": tasks,
            "agent_dispatch_triggered": False,
            "security_testing_authorized": False,
        }
    else:  # overview; no raw rows or source identifiers
        response["data"] = {
            "table_coverage": coverage,
            "questions": list(QUERIES),
            "business_health_verified": False,
            "public_export_requires_separate_approval": True,
        }
    response["limitations"] = sorted(set(response["limitations"]))
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", choices=QUERIES, default="overview")
    parser.add_argument("--db", type=Path, default=Path("auto"),
                        help="local source SQLite path, or auto")
    parser.add_argument("--list-queries", action="store_true")
    args = parser.parse_args(argv)
    if args.list_queries:
        print(json.dumps({"queries": QUERIES, "external_execution_enabled": False}))
        return 0
    try:
        source = resolve_database(args.db)
        result = answer(args.query, snapshot(source))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["answer_status"] == "ready" else 2
    except (SyncError, QueryError, FileNotFoundError, PermissionError) as exc:
        # Do not leak absolute paths or SQL/SQLite internals.
        print("QIL blocked: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
