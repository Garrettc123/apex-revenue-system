import os
import tempfile
import sys

import pytest

from config.tiers import normalize_tier, tier_config
from core import lead_enrichment, payments


def test_normalize_legacy_tiers():
    assert normalize_tier("starter") == "audit"
    assert normalize_tier("pro") == "sprint"
    assert normalize_tier("enterprise") == "retainer"
    assert tier_config("audit")["price_usd"] == 47


def test_enrich_lead_email():
    result = lead_enrichment.enrich_lead({"email": "owner@roofing.example", "full_name": "Pat Owner"})
    assert result["contact"]["email_valid"] is True
    assert result["company"]["domain"] == "roofing.example"


def test_enrichment_quota_gate(tmp_path):
    db = str(tmp_path / "rev.db")
    payments.init_db(db)
    payments.provision_customer(db, "c1", tier="free")
    cfg = tier_config("free")
    for i in range(int(cfg["enrichments_per_month"])):
        payments.record_enrichment(db, "c1", {"i": i}, {"ok": True})
    ok, reason, _ = payments.check_enrichment_access(db, "c1")
    assert ok is False
    assert "limit" in reason.lower()


def test_checkout_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY not configured"):
        payments.create_checkout_session(
            "c1", "audit", "https://example.test", {"audit": "price_x"}, ""
        )


def test_missing_stripe_sdk_never_returns_mock_checkout(monkeypatch):
    monkeypatch.setattr(payments, "require_standing_approval", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "stripe", None)
    with pytest.raises(RuntimeError, match="Stripe SDK is not installed"):
        payments.create_checkout_session(
            "c1", "audit", "https://example.test", {"audit": "price_x"}, "sk_test_dummy"
        )


def test_stripe_event_not_marked_processed_when_customer_identity_is_missing(tmp_path):
    import sqlite3

    db = str(tmp_path / "events.db")
    payments.init_db(db)
    payments.handle_webhook_event(db, {
        "id": "evt_missing_customer",
        "type": "customer.subscription.created",
        "data": {"object": {"metadata": {}}},
    })
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM stripe_events WHERE event_id=?", ("evt_missing_customer",)).fetchone()[0]
    assert count == 0


def test_stripe_event_is_idempotent_after_subscription_is_persisted(tmp_path):
    import sqlite3

    db = str(tmp_path / "events.db")
    payments.init_db(db)
    event = {
        "id": "evt_subscription_created",
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_1",
            "customer": "cus_1",
            "metadata": {"customer_id": "customer-1", "tier": "audit"},
        }},
    }
    payments.handle_webhook_event(db, event)
    payments.handle_webhook_event(db, event)
    assert payments.get_customer(db, "customer-1")["active"] == 1
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM stripe_events WHERE event_id=?", ("evt_subscription_created",)).fetchone()[0]
    assert count == 1
