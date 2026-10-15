"""Regression tests for fail-closed service authentication and webhook handling."""

import base64
import hashlib
import hmac
import json
import time

from flask import Flask

from apex_agent import apex_bootstrap
from core import phase12_api


def test_phase12_write_routes_fail_closed_without_configured_key(monkeypatch, tmp_path):
    monkeypatch.delenv("APEX_API_KEY", raising=False)
    monkeypatch.setenv("REVENUE_DB_FILE", str(tmp_path / "revenue.db"))
    monkeypatch.setattr(phase12_api.vault_inject, "inject_from_vault", lambda: None)

    app = Flask("phase12-auth-test")
    phase12_api.register_phase12_routes(app)
    client = app.test_client()

    calls = [
        ("/api/enrich", {"customer_id": "victim-customer", "lead": {"email": "x@example.test"}}),
        ("/api/enrich/bulk", {"customer_id": "victim-customer", "leads": [{"email": "x@example.test"}]}),
        ("/api/checkout", {"customer_id": "victim-customer", "tier": "audit"}),
    ]
    for path, body in calls:
        response = client.post(path, json=body)
        assert response.status_code == 503, (path, response.get_json())


def test_phase12_write_routes_reject_missing_request_key(monkeypatch, tmp_path):
    monkeypatch.setenv("APEX_API_KEY", "service-test-key")
    monkeypatch.setenv("REVENUE_DB_FILE", str(tmp_path / "revenue.db"))
    monkeypatch.setattr(phase12_api.vault_inject, "inject_from_vault", lambda: None)

    app = Flask("phase12-auth-test")
    phase12_api.register_phase12_routes(app)
    response = app.test_client().post(
        "/api/enrich",
        json={"customer_id": "customer-1", "lead": {"email": "x@example.test"}},
    )
    assert response.status_code == 401


def test_apex_report_requires_service_key(monkeypatch):
    monkeypatch.setenv("APEX_API_KEY", "service-test-key")
    app = Flask("apex-report-auth-test")
    apex_bootstrap.register_apex(app)
    client = app.test_client()

    assert client.get("/apex/report").status_code == 401
    authorized = client.get("/apex/report", headers={"X-API-Key": "service-test-key"})
    assert authorized.status_code == 200


def test_hubspot_webhook_fails_closed_when_secret_missing(monkeypatch):
    monkeypatch.delenv("HUBSPOT_WEBHOOK_SECRET", raising=False)
    app = Flask("hubspot-webhook-test")
    app.config["TESTING"] = True
    apex_bootstrap.register_apex(app)

    response = app.test_client().post(
        "/webhook/hubspot/deal",
        json={"deal": {"id": "deal-1", "domain": "example.test", "dealstage": "closedwon"}},
    )
    assert response.status_code == 503
    assert "HUBSPOT_WEBHOOK_SECRET" in response.get_json()["error"]


def test_hubspot_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("HUBSPOT_WEBHOOK_SECRET", "webhook-test-secret")
    app = Flask("hubspot-webhook-test")
    apex_bootstrap.register_apex(app)

    response = app.test_client().post(
        "/webhook/hubspot/deal",
        data=b'{"deal":{"id":"deal-1"}}',
        headers={
            "Content-Type": "application/json",
            "X-HubSpot-Signature": "not-a-valid-signature",
        },
    )
    assert response.status_code == 401


def test_stripe_webhook_missing_secret_is_service_unavailable(monkeypatch, tmp_path):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("REVENUE_DB_FILE", str(tmp_path / "revenue.db"))
    monkeypatch.setattr(phase12_api.vault_inject, "inject_from_vault", lambda: None)
    app = Flask("stripe-webhook-test")
    phase12_api.register_phase12_routes(app)
    response = app.test_client().post(
        "/api/stripe/webhook",
        data=b'{"id":"evt_1","type":"ping","data":{"object":{}}}',
        headers={"Stripe-Signature": "invalid"},
    )
    assert response.status_code == 503



def test_hubspot_webhook_accepts_valid_legacy_v1_signature(monkeypatch, tmp_path):
    secret = "webhook-test-secret"
    monkeypatch.setenv("HUBSPOT_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("APEX_LEDGER_PATH", str(tmp_path / "apex.db"))
    app = Flask("hubspot-webhook-v1-test")
    apex_bootstrap.register_apex(app)
    payload = {"deal": {"id": "deal-v1", "domain": "example.test", "dealstage": "closedwon"}}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hashlib.sha256(secret.encode("utf-8") + body).hexdigest()
    response = app.test_client().post(
        "/webhook/hubspot/deal",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-HubSpot-Signature": signature,
            "X-HubSpot-Signature-Version": "v1",
        },
    )
    assert response.status_code == 200


def test_hubspot_webhook_accepts_valid_v3_signature(monkeypatch, tmp_path):
    secret = "webhook-test-secret"
    monkeypatch.setenv("HUBSPOT_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("APEX_LEDGER_PATH", str(tmp_path / "apex.db"))
    app = Flask("hubspot-webhook-v3-test")
    apex_bootstrap.register_apex(app)
    payload = {"deal": {"id": "deal-v3", "domain": "example.test", "dealstage": "closedwon"}}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time() * 1000))
    uri = "https://example.test/webhook/hubspot/deal"
    source = "POST" + uri + body.decode("utf-8") + timestamp
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), source.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    response = app.test_client().post(
        "/webhook/hubspot/deal",
        base_url="https://example.test",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-HubSpot-Signature-v3": signature,
            "X-HubSpot-Request-Timestamp": timestamp,
        },
    )
    assert response.status_code == 200


def test_hubspot_v3_rejects_stale_timestamp(monkeypatch):
    secret = "webhook-test-secret"
    monkeypatch.setenv("HUBSPOT_WEBHOOK_SECRET", secret)
    app = Flask("hubspot-webhook-v3-stale-test")
    apex_bootstrap.register_apex(app)
    body = b'{"deal":{"id":"deal-stale"}}'
    stale_timestamp = str(int(time.time() * 1000) - 600_000)
    uri = "http://localhost/webhook/hubspot/deal"
    source = "POST" + uri + body.decode("utf-8") + stale_timestamp
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), source.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    response = app.test_client().post(
        "/webhook/hubspot/deal",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-HubSpot-Signature-v3": signature,
            "X-HubSpot-Request-Timestamp": stale_timestamp,
        },
    )
    assert response.status_code == 401



def test_stripe_webhook_rejects_bad_signature_with_400(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "stripe-test-secret")
    monkeypatch.setenv("REVENUE_DB_FILE", str(tmp_path / "revenue.db"))
    monkeypatch.setattr(phase12_api.vault_inject, "inject_from_vault", lambda: None)
    app = Flask("stripe-webhook-test")
    phase12_api.register_phase12_routes(app)
    response = app.test_client().post(
        "/api/stripe/webhook",
        data=b'{"id":"evt_1","type":"ping","data":{"object":{}}}',
        headers={"Stripe-Signature": "t=1,v1=bad"},
    )
    assert response.status_code == 400


def test_stripe_webhook_fails_closed_when_sdk_missing(monkeypatch, tmp_path):
    import sys
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "stripe-test-secret")
    monkeypatch.setenv("REVENUE_DB_FILE", str(tmp_path / "revenue.db"))
    monkeypatch.setattr(phase12_api.vault_inject, "inject_from_vault", lambda: None)
    monkeypatch.setitem(sys.modules, "stripe", None)
    app = Flask("stripe-webhook-test")
    phase12_api.register_phase12_routes(app)
    response = app.test_client().post(
        "/api/stripe/webhook",
        data=b'{"id":"evt_1","type":"ping","data":{"object":{}}}',
        headers={"Stripe-Signature": "t=1,v1=bad"},
    )
    assert response.status_code == 503
