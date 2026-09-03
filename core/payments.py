from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import sqlite3
from typing import Any

from config.tiers import normalize_tier, tier_config


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                email TEXT,
                tier TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'inactive',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                customer_id TEXT PRIMARY KEY,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                tier TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'inactive',
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS enrichments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                lead_payload TEXT NOT NULL,
                result_payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pipeline_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                name TEXT,
                email TEXT,
                company TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                score REAL NOT NULL,
                enrichment_payload TEXT,
                created_at TEXT NOT NULL,
                next_follow_up_at TEXT,
                FOREIGN KEY(customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS stripe_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def provision_customer(db_path: str, customer_id: str, email: str | None = None, tier: str = "free", status: str = "inactive") -> None:
    now = _utc_now()
    normalized = normalize_tier(tier)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO customers(customer_id, email, tier, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
              email=COALESCE(excluded.email, customers.email),
              tier=excluded.tier,
              status=excluded.status,
              updated_at=excluded.updated_at
            """,
            (customer_id, email, normalized, status, now, now),
        )


def set_subscription(
    db_path: str,
    customer_id: str,
    tier: str,
    active: bool,
    status: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
) -> None:
    provision_customer(db_path, customer_id=customer_id, tier=tier, status=status)
    now = _utc_now()
    normalized = normalize_tier(tier)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO subscriptions(
                customer_id, stripe_customer_id, stripe_subscription_id, tier, status, active, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
              stripe_customer_id=COALESCE(excluded.stripe_customer_id, subscriptions.stripe_customer_id),
              stripe_subscription_id=COALESCE(excluded.stripe_subscription_id, subscriptions.stripe_subscription_id),
              tier=excluded.tier,
              status=excluded.status,
              active=excluded.active,
              updated_at=excluded.updated_at
            """,
            (
                customer_id,
                stripe_customer_id,
                stripe_subscription_id,
                normalized,
                status,
                1 if active else 0,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE customers SET tier=?, status=?, updated_at=? WHERE customer_id=?",
            (normalized, status, now, customer_id),
        )


def get_customer(db_path: str, customer_id: str) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT c.customer_id, c.email,
                   COALESCE(s.tier, c.tier, 'free') AS tier,
                   COALESCE(s.status, c.status, 'inactive') AS status,
                   COALESCE(s.active, 0) AS active
            FROM customers c
            LEFT JOIN subscriptions s ON s.customer_id = c.customer_id
            WHERE c.customer_id = ?
            """,
            (customer_id,),
        ).fetchone()
    if not row:
        return {
            "customer_id": customer_id,
            "email": None,
            "tier": "free",
            "status": "inactive",
            "active": 0,
        }
    return dict(row)


def record_enrichment(db_path: str, customer_id: str, lead_payload: dict[str, Any], result_payload: dict[str, Any]) -> int:
    now = _utc_now()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO enrichments(customer_id, lead_payload, result_payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (customer_id, json.dumps(lead_payload), json.dumps(result_payload), now),
        )
        return int(cur.lastrowid)


def enrichments_this_month(db_path: str, customer_id: str) -> int:
    month_prefix = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM enrichments WHERE customer_id=? AND substr(created_at, 1, 7)=?",
            (customer_id, month_prefix),
        ).fetchone()
    return int(row["c"] if row else 0)


def enrichments_today_count(db_path: str) -> int:
    today_prefix = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM enrichments WHERE substr(created_at, 1, 10)=?",
            (today_prefix,),
        ).fetchone()
    return int(row["c"] if row else 0)


def stripe_customer_count(db_path: str) -> int:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()
    return int(row["c"] if row else 0)


def check_enrichment_access(db_path: str, customer_id: str) -> tuple[bool, str, dict[str, Any]]:
    customer = get_customer(db_path, customer_id)
    tier = customer["tier"]
    cfg = tier_config(tier)
    limit = cfg["enrichments_per_month"]
    used = enrichments_this_month(db_path, customer_id)
    if limit is not None and used >= int(limit):
        return False, "Monthly enrichment limit reached for tier", customer
    return True, "ok", customer


def create_checkout_session(
    customer_id: str,
    tier: str,
    base_url: str,
    price_ids: dict[str, str],
    stripe_secret_key: str,
) -> dict[str, Any]:
    normalized_tier = normalize_tier(tier)
    if normalized_tier == "free":
        raise ValueError("Free tier does not require checkout")
    if not stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY not configured")
    price_id = price_ids.get(normalized_tier, "")
    if not price_id:
        raise RuntimeError(f"Missing Stripe price id for tier: {normalized_tier}")

    success_url = f"{base_url}/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base_url}/?cancelled=1"

    try:
        import stripe

        stripe.api_key = stripe_secret_key
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=customer_id,
            metadata={"customer_id": customer_id, "tier": normalized_tier},
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return {"id": session.get("id"), "url": session.get("url"), "tier": normalized_tier}
    except ImportError:
        return {
            "id": f"cs_mock_{customer_id}_{normalized_tier}",
            "url": f"{base_url}/checkout/mock/{normalized_tier}",
            "tier": normalized_tier,
        }


def _parse_signature(sig_header: str) -> tuple[str, list[str]]:
    timestamp = ""
    signatures: list[str] = []
    for part in (sig_header or "").split(","):
        token = part.strip()
        if token.startswith("t="):
            timestamp = token[2:]
        elif token.startswith("v1="):
            signatures.append(token[3:])
    return timestamp, signatures


def verify_webhook_signature(payload: bytes, sig_header: str, webhook_secret: str) -> dict[str, Any]:
    if not webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")

    try:
        import stripe

        return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ImportError:
        ts, signatures = _parse_signature(sig_header)
        if not ts or not signatures:
            raise ValueError("Invalid Stripe signature")
        signed_payload = f"{ts}.{payload.decode('utf-8')}".encode("utf-8")
        digest = hmac.new(webhook_secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(digest, sig) for sig in signatures):
            raise ValueError("Invalid Stripe signature")
        return json.loads(payload.decode("utf-8"))


def handle_webhook_event(db_path: str, event: dict[str, Any]) -> None:
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    data_obj = event.get("data", {}).get("object", {}) if isinstance(event.get("data"), dict) else {}

    if event_id:
        with _connect(db_path) as conn:
            existing = conn.execute("SELECT event_id FROM stripe_events WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                return
            conn.execute(
                "INSERT INTO stripe_events(event_id, event_type, created_at) VALUES (?, ?, ?)",
                (event_id, event_type or "unknown", _utc_now()),
            )

    if event_type == "checkout.session.completed":
        metadata = data_obj.get("metadata") if isinstance(data_obj.get("metadata"), dict) else {}
        customer_id = str(
            metadata.get("customer_id")
            or data_obj.get("client_reference_id")
            or data_obj.get("customer")
            or ""
        ).strip()
        if not customer_id:
            return
        tier = normalize_tier(metadata.get("tier") or "starter")
        email = data_obj.get("customer_details", {}).get("email") if isinstance(data_obj.get("customer_details"), dict) else None
        set_subscription(
            db_path,
            customer_id=customer_id,
            tier=tier,
            active=True,
            status="active",
            stripe_customer_id=str(data_obj.get("customer") or "") or None,
            stripe_subscription_id=str(data_obj.get("subscription") or "") or None,
        )
        if email:
            provision_customer(db_path, customer_id=customer_id, email=email, tier=tier, status="active")

    elif event_type == "customer.subscription.created":
        metadata = data_obj.get("metadata") if isinstance(data_obj.get("metadata"), dict) else {}
        customer_id = str(metadata.get("customer_id") or data_obj.get("customer") or "").strip()
        if not customer_id:
            return
        tier = normalize_tier(metadata.get("tier") or "starter")
        set_subscription(
            db_path,
            customer_id=customer_id,
            tier=tier,
            active=True,
            status="active",
            stripe_customer_id=str(data_obj.get("customer") or "") or None,
            stripe_subscription_id=str(data_obj.get("id") or "") or None,
        )

    elif event_type == "customer.subscription.deleted":
        metadata = data_obj.get("metadata") if isinstance(data_obj.get("metadata"), dict) else {}
        customer_id = str(metadata.get("customer_id") or data_obj.get("customer") or "").strip()
        if not customer_id:
            return
        set_subscription(
            db_path,
            customer_id=customer_id,
            tier="free",
            active=False,
            status="cancelled",
            stripe_customer_id=str(data_obj.get("customer") or "") or None,
            stripe_subscription_id=str(data_obj.get("id") or "") or None,
        )

    elif event_type == "invoice.payment_failed":
        metadata = data_obj.get("metadata") if isinstance(data_obj.get("metadata"), dict) else {}
        customer_id = str(metadata.get("customer_id") or data_obj.get("customer") or "").strip()
        if not customer_id:
            return
        current = get_customer(db_path, customer_id)
        set_subscription(
            db_path,
            customer_id=customer_id,
            tier=current.get("tier", "free"),
            active=False,
            status="past_due",
            stripe_customer_id=str(data_obj.get("customer") or "") or None,
            stripe_subscription_id=str(data_obj.get("subscription") or "") or None,
        )
