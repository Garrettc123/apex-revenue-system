import os
import tempfile

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


def test_checkout_mock_without_stripe(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    try:
        payments.create_checkout_session(
            "c1", "audit", "https://example.test", {"audit": "price_x"}, ""
        )
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
