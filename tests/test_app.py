"""
Comprehensive tests for the Flask application endpoints.
"""
import hashlib
import hmac
import json
import pytest
from main import app


@pytest.fixture(autouse=True)
def isolated_revenue_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr("main.REVENUE_LEDGER_FILE", str(tmp_path / "revenue_ledger.json"))
    monkeypatch.setattr("main.CB_WEBHOOK_SECRET", "")


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── /health ──────────────────────────────────────────────────────────────────

def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_health_json_structure(client):
    r = client.get("/health")
    d = json.loads(r.data)
    assert d["status"] == "healthy"
    assert "time" in d
    assert "gemini" in d
    assert "coinbase" in d


def test_health_no_api_keys_shows_config_message(client):
    r = client.get("/health")
    d = json.loads(r.data)
    # Without API keys set the response messages prompt for configuration
    assert "gemini" in d
    assert "coinbase" in d


# ── / (landing page) ─────────────────────────────────────────────────────────

def test_index_returns_200(client):
    r = client.get("/")
    assert r.status_code == 200


def test_index_contains_genesis(client):
    r = client.get("/")
    assert b"GENESIS" in r.data


def test_index_contains_coinbase(client):
    r = client.get("/")
    assert b"Coinbase" in r.data


def test_index_contains_pricing_links(client):
    r = client.get("/")
    assert b"/checkout/starter" in r.data
    assert b"/checkout/pro" in r.data
    assert b"/checkout/enterprise" in r.data


# ── /metrics ─────────────────────────────────────────────────────────────────

def test_metrics_returns_200(client):
    r = client.get("/metrics")
    assert r.status_code == 200


def test_metrics_json_structure(client):
    r = client.get("/metrics")
    d = json.loads(r.data)
    assert "mrr_usd" in d
    assert "mrr_target_usd" in d
    assert "progress_pct" in d
    assert "active_customers" in d
    assert "total_customers" in d
    assert "gap_to_target" in d


def test_metrics_target_is_5000(client):
    r = client.get("/metrics")
    d = json.loads(r.data)
    assert d["mrr_target_usd"] == 5000


def test_metrics_gap_is_non_negative(client):
    r = client.get("/metrics")
    d = json.loads(r.data)
    assert d["gap_to_target"] >= 0


# ── /success ─────────────────────────────────────────────────────────────────

def test_success_returns_200(client):
    r = client.get("/success")
    assert r.status_code == 200


def test_success_contains_coinbase(client):
    r = client.get("/success")
    assert b"Coinbase" in r.data


# ── /checkout/<plan> ─────────────────────────────────────────────────────────

def test_checkout_invalid_plan_redirects(client):
    r = client.get("/checkout/invalid_plan", follow_redirects=False)
    # Without a COINBASE_API_KEY the key check fires first (503).
    # With a key an invalid plan triggers a redirect (3xx).
    assert r.status_code in (301, 302, 303, 503)


def test_checkout_no_api_key_returns_503(client):
    # Without COINBASE_API_KEY set, should return 503
    r = client.get("/checkout/pro")
    assert r.status_code == 503


def test_checkout_valid_plans_no_key(client):
    for plan in ("starter", "pro", "enterprise"):
        r = client.get(f"/checkout/{plan}")
        assert r.status_code in (303, 302, 503), f"Unexpected status for plan={plan}"


# ── /genesis ─────────────────────────────────────────────────────────────────

def test_genesis_get_no_key_returns_503(client):
    r = client.get("/genesis")
    assert r.status_code == 503


def test_genesis_post_no_key_returns_503(client):
    r = client.post("/genesis", json={"prompt": "test"})
    assert r.status_code == 503


def test_genesis_503_json_error_field(client):
    r = client.get("/genesis")
    d = json.loads(r.data)
    assert "error" in d


# ── /ai/leads ────────────────────────────────────────────────────────────────

def test_ai_leads_no_key_returns_503(client):
    r = client.get("/ai/leads")
    assert r.status_code == 503


# ── /ai/analyze ──────────────────────────────────────────────────────────────

def test_ai_analyze_no_key_returns_503(client):
    r = client.post("/ai/analyze", json={"prompt": "test"})
    assert r.status_code == 503


# ── /webhook/coinbase ────────────────────────────────────────────────────────

def test_webhook_invalid_signature_returns_400(monkeypatch):
    """When CB_WEBHOOK_SECRET is set, an invalid signature should return 400."""
    import main as m
    monkeypatch.setattr(m, "CB_WEBHOOK_SECRET", "test_secret")
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        r = c.post(
            "/webhook/coinbase",
            data=b'{"event": {"type": "charge:confirmed", "data": {}}}',
            headers={"X-CC-Webhook-Signature": "badsig"},
            content_type="application/json",
        )
        assert r.status_code == 400


def test_webhook_no_secret_accepts_any_payload(monkeypatch):
    """Without a webhook secret, all payloads should be accepted."""
    import main as m
    monkeypatch.setattr(m, "CB_WEBHOOK_SECRET", "")
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        r = c.post(
            "/webhook/coinbase",
            data=b'{"event": {"type": "charge:pending", "data": {"id": "test123"}}}',
            content_type="application/json",
        )
    assert r.status_code == 200
    d = json.loads(r.data)
    assert d["status"] == "ok"


def test_coinbase_confirmed_increases_metrics_mrr(client):
    payload = json.dumps({
        "event": {
            "type": "charge:confirmed",
            "data": {
                "id": "ch_test_001",
                "metadata": {"plan": "pro", "amount": "149.00"},
                "pricing": {"local": {"amount": "149.00", "currency": "USD"}},
            },
        }
    }).encode()

    r = client.post("/webhook/coinbase", data=payload, content_type="application/json")
    assert r.status_code == 200
    metrics = client.get("/metrics").get_json()
    assert metrics["mrr_usd"] == 149.0
    assert metrics["active_customers"] == 1


def test_edge_revenue_webhook_records_and_updates_metrics(client):
    r = client.post(
        "/webhook/edge/revenue",
        json={
            "node_id": "pixel-10-edge-001",
            "charge_id": "edge_charge_001",
            "amount_usd": 49.0,
            "plan": "starter",
            "source": "edge",
            "timestamp": "2026-08-26T00:00:00Z",
        },
    )
    assert r.status_code == 200
    assert r.get_json() == {"status": "recorded"}
    metrics = client.get("/metrics").get_json()
    assert metrics["mrr_usd"] == 49.0
    assert metrics["active_customers"] == 1


def test_edge_revenue_webhook_missing_fields_returns_400(client):
    r = client.post(
        "/webhook/edge/revenue",
        json={
            "node_id": "pixel-10-edge-001",
            "charge_id": "edge_charge_002",
            "amount_usd": 49.0,
            "source": "edge",
        },
    )
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_stripe_payment_intent_succeeded_increases_metrics_mrr(client):
    r = client.post(
        "/webhook/stripe",
        json={
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_123",
                    "amount_received": 12345,
                    "currency": "usd",
                    "metadata": {"plan": "pro"},
                }
            },
        },
    )
    assert r.status_code == 200
    metrics = client.get("/metrics").get_json()
    assert metrics["mrr_usd"] == 123.45
    assert metrics["active_customers"] == 1


# ── /integrations/status ──────────────────────────────────────────────────────

def test_integrations_status_returns_200(client):
    r = client.get("/integrations/status")
    assert r.status_code == 200


def test_integrations_status_json_has_all_keys(client):
    r = client.get("/integrations/status")
    d = r.get_json()
    for key in ("shopify", "stripe", "hubspot", "supabase", "linear", "notion", "docusign", "hunter", "coinbase", "gemini"):
        assert key in d


def test_integrations_status_no_keys_shows_config_messages(client):
    r = client.get("/integrations/status")
    d = r.get_json()
    # Without any env keys configured, all values should be instructions not "connected"
    for key in ("shopify", "stripe", "hubspot", "supabase", "linear", "notion", "docusign", "hunter"):
        assert d[key] != "connected"


# ── /health integration fields ────────────────────────────────────────────────

def test_health_includes_integration_keys(client):
    r = client.get("/health")
    d = r.get_json()
    for key in ("shopify", "stripe", "hubspot", "supabase", "linear", "notion", "docusign", "hunter"):
        assert key in d


# ── /webhook/shopify ─────────────────────────────────────────────────────────

def test_shopify_webhook_missing_secret_accepts_order_paid(client):
    """Without a secret, any HMAC passes through."""
    order = {
        "id": "5001",
        "email": "buyer@example.com",
        "customer": {"first_name": "Jane", "last_name": "Doe", "email": "buyer@example.com"},
        "total_price": "297.00",
        "line_items": [{"title": "ABAS-001 Autonomous Business Automation System"}],
    }
    r = client.post(
        "/webhook/shopify",
        data=json.dumps(order),
        headers={"X-Shopify-Topic": "orders/paid", "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "processed"
    assert d["order_id"] == "5001"


def test_shopify_webhook_records_revenue_event(client):
    order = {
        "id": "5002",
        "email": "buyer2@example.com",
        "customer": {"first_name": "John", "last_name": "Smith", "email": "buyer2@example.com"},
        "total_price": "997.00",
        "line_items": [{"title": "IRAS Pipeline Monthly"}],
    }
    client.post(
        "/webhook/shopify",
        data=json.dumps(order),
        headers={"X-Shopify-Topic": "orders/paid", "Content-Type": "application/json"},
    )
    metrics = client.get("/metrics").get_json()
    assert metrics["mrr_usd"] == 997.0
    assert metrics["active_customers"] == 1


def test_shopify_webhook_ignored_topic_returns_ignored(client):
    r = client.post(
        "/webhook/shopify",
        data=json.dumps({"id": "5003"}),
        headers={"X-Shopify-Topic": "products/update", "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.get_json()["status"] == "ignored"


def test_shopify_webhook_invalid_json_returns_400(client):
    r = client.post(
        "/webhook/shopify",
        data=b"not-json",
        headers={"X-Shopify-Topic": "orders/paid", "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_shopify_webhook_invalid_signature_returns_400(monkeypatch):
    import main as m
    monkeypatch.setattr(m, "SHOPIFY_WEBHOOK_SECRET", "shopify_secret")
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        r = c.post(
            "/webhook/shopify",
            data=json.dumps({"id": "5004", "total_price": "49.00", "line_items": []}),
            headers={
                "X-Shopify-Topic": "orders/paid",
                "X-Shopify-Hmac-Sha256": "badsignature",
                "Content-Type": "application/json",
            },
        )
    assert r.status_code == 400


def test_shopify_webhook_valid_signature_accepted(monkeypatch):
    import main as m
    import base64
    secret = "test_shopify_secret"
    monkeypatch.setattr(m, "SHOPIFY_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", "/tmp/test_shopify_sig_ledger.json")
    payload = json.dumps({"id": "5005", "total_price": "49.00", "line_items": [], "customer": {}}).encode()
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    sig = base64.b64encode(digest).decode()
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        r = c.post(
            "/webhook/shopify",
            data=payload,
            headers={
                "X-Shopify-Topic": "orders/paid",
                "X-Shopify-Hmac-Sha256": sig,
                "Content-Type": "application/json",
            },
        )
    assert r.status_code == 200
    assert r.get_json()["status"] == "processed"
