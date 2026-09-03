from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def score_prospect(prospect: dict[str, Any], enrichment: dict[str, Any] | None = None) -> float:
    enrichment = enrichment or {}
    score = 25.0
    if prospect.get("budget_usd"):
        try:
            budget = float(prospect.get("budget_usd") or 0)
            if budget >= 10000:
                score += 35
            elif budget >= 2000:
                score += 20
            else:
                score += 8
        except (TypeError, ValueError):
            pass
    if prospect.get("company_size"):
        try:
            size = int(prospect.get("company_size") or 0)
            if size >= 200:
                score += 20
            elif size >= 20:
                score += 12
            else:
                score += 5
        except (TypeError, ValueError):
            pass

    email_valid = bool(enrichment.get("contact", {}).get("email_valid")) if isinstance(enrichment, dict) else False
    if email_valid:
        score += 15

    if prospect.get("decision_timeline_days"):
        try:
            timeline = int(prospect.get("decision_timeline_days") or 0)
            if timeline <= 30:
                score += 10
            elif timeline <= 90:
                score += 5
        except (TypeError, ValueError):
            pass

    return round(max(0.0, min(100.0, score)), 2)


def add_prospect(
    db_path: str,
    customer_id: str,
    prospect: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
    status: str = "new",
) -> dict[str, Any]:
    score = score_prospect(prospect, enrichment)
    next_follow_up = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)).isoformat()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO pipeline_leads(
                customer_id, name, email, company, status, score, enrichment_payload, created_at, next_follow_up_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                prospect.get("name"),
                prospect.get("email"),
                prospect.get("company"),
                status,
                score,
                json.dumps(enrichment or {}),
                _utc_now(),
                next_follow_up,
            ),
        )
        lead_id = int(cur.lastrowid)
    return {
        "id": lead_id,
        "customer_id": customer_id,
        "name": prospect.get("name"),
        "email": prospect.get("email"),
        "company": prospect.get("company"),
        "status": status,
        "score": score,
        "next_follow_up_at": next_follow_up,
    }


def list_leads(db_path: str, customer_id: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, customer_id, name, email, company, status, score, created_at, next_follow_up_at
            FROM pipeline_leads
            WHERE customer_id=?
            ORDER BY id DESC
            """,
            (customer_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def pipeline_stats(db_path: str, customer_id: str | None = None) -> dict[str, Any]:
    where = ""
    params: tuple[Any, ...] = ()
    if customer_id:
        where = " WHERE customer_id=?"
        params = (customer_id,)

    with _connect(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM pipeline_leads{where}", params).fetchone()["c"]
        won = conn.execute(
            f"SELECT COUNT(*) AS c FROM pipeline_leads{where + (' AND ' if where else ' WHERE ') + 'status=\'won\''}",
            params,
        ).fetchone()["c"]
        avg_score = conn.execute(f"SELECT AVG(score) AS avg_score FROM pipeline_leads{where}", params).fetchone()["avg_score"]

    conversion = (float(won) / float(total) * 100.0) if total else 0.0
    return {
        "total_leads": int(total),
        "won_leads": int(won),
        "conversion_rate_pct": round(conversion, 2),
        "avg_score": round(float(avg_score or 0.0), 2),
    }
