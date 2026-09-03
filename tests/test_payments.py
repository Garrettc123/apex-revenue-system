import hashlib
import hmac
import json
import builtins

import pytest

import main as m
from core import payments


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "revenue_engine.db")
    monkeypatch.setattr(m, "REVENUE_DB_FILE", db_path)
    payments.init_db(db_path)
    monkeypatch.setattr(m, "STRIPE_PRICE_ID_STARTER", "price_starter")
    monkeypatch.setattr(m, "STRIPE_PRICE_ID_PRO", "price_pro")
    monkeypatch.setattr(m, "STRIPE_PRICE_ID_ENTERPRISE", "price_enterprise")


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        yield c


def test_api_checkout_requires_stripe_key(client):
    r = client.post("/api/checkout", json={"customer_id": "cust_1", "tier": "starter"})
    assert r.status_code == 503


def test_stripe_webhook_signature_verification_and_customer_provisioning(client, monkeypatch):
    monkeypatch.setattr(m, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    event = {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_stripe_1",
                "subscription": "sub_1",
                "metadata": {"customer_id": "cust_100", "tier": "starter"},
                "customer_details": {"email": "paid@example.com"},
            }
        },
    }

    monkeypatch.setattr(payments, "verify_webhook_signature", lambda payload, sig_header, webhook_secret: event)

    r = client.post("/api/stripe/webhook", data=json.dumps(event), headers={"Stripe-Signature": "t=1,v1=abc"})
    assert r.status_code == 200

    customer = payments.get_customer(m.REVENUE_DB_FILE, "cust_100")
    assert customer["tier"] == "starter"
    assert customer["status"] == "active"


def test_invoice_payment_failed_sets_customer_past_due(client, monkeypatch):
    monkeypatch.setattr(m, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    payments.set_subscription(m.REVENUE_DB_FILE, "cust_200", tier="pro", active=True, status="active")

    event = {
        "id": "evt_2",
        "type": "invoice.payment_failed",
        "data": {"object": {"customer": "cust_200", "subscription": "sub_200", "metadata": {"customer_id": "cust_200"}}},
    }
    monkeypatch.setattr(payments, "verify_webhook_signature", lambda payload, sig_header, webhook_secret: event)

    r = client.post("/api/stripe/webhook", data=json.dumps(event), headers={"Stripe-Signature": "t=1,v1=abc"})
    assert r.status_code == 200

    customer = payments.get_customer(m.REVENUE_DB_FILE, "cust_200")
    assert customer["status"] == "past_due"


def test_verify_webhook_signature_hmac_fallback(monkeypatch):
    original_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if name == "stripe":
            raise ImportError("forced in test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)
    payload = b'{"id":"evt_fallback","type":"checkout.session.completed"}'
    secret = "whsec_local"
    timestamp = "1893456000"
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    sig_header = f"t={timestamp},v1={digest}"

    event = payments.verify_webhook_signature(payload, sig_header, secret)
    assert event["id"] == "evt_fallback"
