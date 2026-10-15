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
    # Default: secrets unset → webhooks fail closed (503)
    monkeypatch.setattr("main.CB_WEBHOOK_SECRET", "")
    monkeypatch.setattr("main.EDGE_WEBHOOK_SECRET", "")
    monkeypatch.setattr("main.STRIPE_WEBHOOK_SECRET", "")
    monkeypatch.setattr("main.SHOPIFY_WEBHOOK_SECRET", "")
    monkeypatch.setenv("APEX_API_KEY", "test-apex-api-key")


@pytest.fixture
def client(monkeypatch):
    # Internal routes remain protected in tests; use an explicit test-only key.
    monkeypatch.setenv("APEX_API_KEY", "test-apex-api-key")
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        yield c


def _edge_sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _shopify_post(client, monkeypatch, payload, topic, secret="shopify-test-secret"):
    import base64
    import main as m
    monkeypatch.setattr(m, "SHOPIFY_WEBHOOK_SECRET", secret)
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return client.post(
        "/webhook/shopify",
        data=body,
        headers={
            "X-Shopify-Topic": topic,
            "X-Shopify-Hmac-Sha256": signature,
            "Content-Type": "application/json",
        },
    )


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
    assert r.status_code in (301, 302, 303, 503)


def test_checkout_no_api_key_returns_503(client):
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

def test_webhook_missing_secret_returns_503(client):
    r = client.post(
        "/webhook/coinbase",
        data=b'{"event": {"type": "charge:pending", "data": {}}}',
        content_type="application/json",
    )
    assert r.status_code == 503


def test_webhook_invalid_signature_returns_400(monkeypatch):
    import main as m
    monkeypatch.setattr(m, "CB_WEBHOOK_SECRET", "test_secret")
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        r = c.post(
            "/webhook/coinbase",
            data=b'{"event": {"type": "charge:confirmed", "data": {}}}',
            headers={"X-CC-Webhook-Signature": "badsig"},
            content_type="application/json",
        )
        assert r.status_code == 400


def test_coinbase_confirmed_increases_metrics_mrr(monkeypatch, tmp_path):
    import main as m
    secret = "cb_test_secret"
    monkeypatch.setattr(m, "CB_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    m.app.config["TESTING"] = True
    payload = json.dumps({
        "event": {
            "type": "charge:confirmed",
            "data": {
                "id": "ch_test_001",
                "metadata": {"plan": "pro", "amount": "149.00", "customer_id": "cust-cb-1"},
                "pricing": {"local": {"amount": "149.00", "currency": "USD"}},
            },
        }
    }).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        r = c.post(
            "/webhook/coinbase",
            data=payload,
            headers={"X-CC-Webhook-Signature": sig},
            content_type="application/json",
        )
        assert r.status_code == 200
        metrics = c.get("/metrics").get_json()
        assert metrics["mrr_usd"] == 0.0
        assert metrics["one_time_revenue_usd"] == 149.0
        assert metrics["confirmed_revenue_usd"] == 149.0
        assert metrics["active_customers"] == 1


def test_edge_revenue_requires_secret(client):
    r = client.post(
        "/webhook/edge/revenue",
        json={
            "node_id": "pixel-10-edge-001",
            "charge_id": "edge_charge_001",
            "amount_usd": 49.0,
            "plan": "starter",
            "source": "edge",
        },
    )
    assert r.status_code == 503


def test_edge_revenue_webhook_records_and_updates_metrics(monkeypatch, tmp_path):
    import main as m
    secret = "edge_secret"
    monkeypatch.setattr(m, "EDGE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    m.app.config["TESTING"] = True
    body = json.dumps({
        "node_id": "pixel-10-edge-001",
        "customer_id": "cust-edge-1",
        "charge_id": "edge_charge_001",
        "amount_usd": 49.0,
        "plan": "starter",
        "source": "edge",
        "timestamp": "2026-08-26T00:00:00Z",
    }).encode()
    sig = _edge_sig(secret, body)
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        r = c.post(
            "/webhook/edge/revenue",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Edge-Signature": sig,
            },
        )
        assert r.status_code == 200
        assert r.get_json() == {"status": "recorded"}
        metrics = c.get("/metrics").get_json()
        assert metrics["mrr_usd"] == 49.0
        assert metrics["active_customers"] == 1


def test_edge_revenue_idempotent_on_charge_id(monkeypatch, tmp_path):
    import main as m
    secret = "edge_secret"
    monkeypatch.setattr(m, "EDGE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    m.app.config["TESTING"] = True
    body = json.dumps({
        "node_id": "pixel-10-edge-001",
        "customer_id": "cust-edge-1",
        "charge_id": "edge_dup_001",
        "amount_usd": 10.0,
        "plan": "starter",
        "source": "edge",
    }).encode()
    sig = _edge_sig(secret, body)
    headers = {"Content-Type": "application/json", "X-Edge-Signature": sig}
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        r1 = c.post("/webhook/edge/revenue", data=body, headers=headers)
        r2 = c.post("/webhook/edge/revenue", data=body, headers=headers)
        assert r1.get_json()["status"] == "recorded"
        assert r2.get_json()["status"] == "duplicate"
        metrics = c.get("/metrics").get_json()
        assert metrics["mrr_usd"] == 10.0
        assert metrics["active_customers"] == 1


def test_edge_revenue_rejects_non_finite(monkeypatch, tmp_path):
    import main as m
    secret = "edge_secret"
    monkeypatch.setattr(m, "EDGE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    m.app.config["TESTING"] = True
    body = json.dumps({
        "node_id": "pixel-10-edge-001",
        "charge_id": "edge_nan",
        "amount_usd": "NaN",
        "plan": "starter",
        "source": "edge",
    }).encode()
    sig = _edge_sig(secret, body)
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        r = c.post(
            "/webhook/edge/revenue",
            data=body,
            headers={"Content-Type": "application/json", "X-Edge-Signature": sig},
        )
        assert r.status_code == 400


def test_edge_revenue_webhook_missing_fields_returns_400(monkeypatch, tmp_path):
    import main as m
    secret = "edge_secret"
    monkeypatch.setattr(m, "EDGE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    m.app.config["TESTING"] = True
    body = json.dumps({
        "node_id": "pixel-10-edge-001",
        "charge_id": "edge_charge_002",
        "amount_usd": 49.0,
        "source": "edge",
    }).encode()
    sig = _edge_sig(secret, body)
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        r = c.post(
            "/webhook/edge/revenue",
            data=body,
            headers={"Content-Type": "application/json", "X-Edge-Signature": sig},
        )
        assert r.status_code == 400
        assert "error" in r.get_json()


def test_stripe_missing_secret_returns_503(client):
    r = client.post(
        "/webhook/stripe",
        json={"type": "payment_intent.succeeded", "data": {"object": {}}},
    )
    assert r.status_code == 503


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

def test_shopify_webhook_missing_secret_fails_closed(client, monkeypatch):
    import main as m
    monkeypatch.setattr(m, "SHOPIFY_WEBHOOK_SECRET", "")
    r = client.post(
        "/webhook/shopify",
        data=json.dumps({"id": "5001", "total_price": "297.00"}).encode(),
        headers={"X-Shopify-Topic": "orders/paid", "Content-Type": "application/json"},
    )
    assert r.status_code == 503
    assert "SHOPIFY_WEBHOOK_SECRET" in r.get_json()["error"]


def test_shopify_webhook_records_revenue_event(client, monkeypatch):
    order = {
        "id": "5002",
        "email": "buyer2@example.com",
        "customer": {"first_name": "John", "last_name": "Smith", "email": "buyer2@example.com"},
        "total_price": "997.00",
        "line_items": [{"title": "IRAS Pipeline Monthly"}],
    }
    _shopify_post(client, monkeypatch, order, "orders/paid")
    metrics = client.get("/metrics").get_json()
    assert metrics["mrr_usd"] == 0.0
    assert metrics["one_time_revenue_usd"] == 997.0
    assert metrics["confirmed_revenue_usd"] == 997.0
    assert metrics["active_customers"] == 1


def test_shopify_webhook_ignored_topic_returns_ignored(client, monkeypatch):
    r = _shopify_post(client, monkeypatch, {"id": "5003"}, "products/update")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ignored"


def test_shopify_webhook_invalid_json_returns_400(client, monkeypatch):
    r = _shopify_post(client, monkeypatch, b"not-json", "orders/paid")
    assert r.status_code == 400


def test_shopify_webhook_invalid_signature_returns_400(monkeypatch):
    import main as m
    monkeypatch.setattr(m, "SHOPIFY_WEBHOOK_SECRET", "shopify_secret")
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
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
    payload = json.dumps({"id": "5005", "total_price": "49.00", "line_items": [], "customer": {}}).encode()
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    sig = base64.b64encode(digest).decode()
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
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



def test_metrics_never_infer_mrr_from_legacy_one_time_events(client, tmp_path, monkeypatch):
    import main as m
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "legacy-ledger.json"))
    m.record_revenue_event(
        event_type="shopify.order.paid",
        amount_usd=100.0,
        plan="unknown plan",
        charge_id="legacy-order-1",
        source="shopify",
        status="confirmed",
    )
    metrics = client.get("/metrics").get_json()
    assert metrics["mrr_usd"] == 0.0
    assert metrics["confirmed_revenue_usd"] == 100.0
    assert metrics["one_time_revenue_usd"] == 100.0


def test_stripe_charge_succeeded_is_not_double_counted(monkeypatch, tmp_path):
    import main as m
    import stripe

    monkeypatch.setattr(m, "STRIPE_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "stripe-ledger.json"))
    monkeypatch.setattr(
        stripe.Webhook, "construct_event",
        lambda *args, **kwargs: {
            "id": "evt_charge_succeeded",
            "type": "charge.succeeded",
            "data": {"object": {"id": "ch_1", "amount": 14900, "currency": "usd"}},
        },
    )
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        response = c.post(
            "/webhook/stripe",
            data=b'{"id":"evt_charge_succeeded","type":"charge.succeeded","data":{"object":{"id":"ch_1"}}}',
            headers={"Stripe-Signature": "test-signature"},
        )
        assert response.status_code == 200
        metrics = c.get("/metrics").get_json()
        assert metrics["confirmed_revenue_usd"] == 0.0
        assert metrics["mrr_usd"] == 0.0


def test_stripe_payment_intent_is_one_time_revenue(monkeypatch, tmp_path):
    import main as m
    import stripe

    monkeypatch.setattr(m, "STRIPE_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "stripe-ledger.json"))
    monkeypatch.setattr(
        stripe.Webhook, "construct_event",
        lambda *args, **kwargs: {
            "id": "evt_payment_intent",
            "type": "payment_intent.succeeded",
            "data": {"object": {
                "id": "pi_1",
                "amount": 14900,
                "amount_received": 14900,
                "currency": "usd",
                "customer": "cus_one_time",
                "metadata": {"plan": "one-time"},
            }},
        },
    )
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        response = c.post(
            "/webhook/stripe",
            data=b'{"id":"evt_payment_intent","type":"payment_intent.succeeded","data":{"object":{"id":"pi_1"}}}',
            headers={"Stripe-Signature": "test-signature"},
        )
        assert response.status_code == 200
        metrics = c.get("/metrics").get_json()
        assert metrics["confirmed_revenue_usd"] == 149.0
        assert metrics["one_time_revenue_usd"] == 149.0
        assert metrics["mrr_usd"] == 0.0
        assert metrics["active_customers"] == 1


def test_stripe_invoice_paid_contributes_monthly_equivalent_mrr(monkeypatch, tmp_path):
    import main as m
    import stripe

    monkeypatch.setattr(m, "STRIPE_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "stripe-ledger.json"))
    monkeypatch.setattr(
        stripe.Webhook, "construct_event",
        lambda *args, **kwargs: {
            "id": "evt_invoice_paid",
            "type": "invoice.paid",
            "data": {"object": {
                "id": "in_1",
                "amount_paid": 14900,
                "currency": "usd",
                "customer": "cus_sub",
                "subscription": "sub_1",
                "lines": {"data": [
                    {"price": {"recurring": {"interval": "month", "interval_count": 1}}}
                ]},
            }},
        },
    )
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        response = c.post(
            "/webhook/stripe",
            data=b'{"id":"evt_invoice_paid","type":"invoice.paid","data":{"object":{"id":"in_1"}}}',
            headers={"Stripe-Signature": "test-signature"},
        )
        assert response.status_code == 200
        metrics = c.get("/metrics").get_json()
        assert metrics["confirmed_revenue_usd"] == 149.0
        assert metrics["mrr_usd"] == 149.0
        assert metrics["one_time_revenue_usd"] == 0.0
        assert metrics["active_customers"] == 1


def test_annual_invoice_is_normalized_to_monthly_recurring_revenue(monkeypatch, tmp_path):
    import main as m
    import stripe

    monkeypatch.setattr(m, "STRIPE_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "stripe-ledger.json"))
    monkeypatch.setattr(
        stripe.Webhook, "construct_event",
        lambda *args, **kwargs: {
            "id": "evt_annual_invoice",
            "type": "invoice.paid",
            "data": {"object": {
                "id": "in_annual",
                "amount_paid": 120000,
                "currency": "usd",
                "customer": "cus_annual",
                "subscription": "sub_annual",
                "lines": {"data": [
                    {"price": {"recurring": {"interval": "year", "interval_count": 1}}}
                ]},
            }},
        },
    )
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        c.environ_base["HTTP_X_API_KEY"] = "test-apex-api-key"
        response = c.post(
            "/webhook/stripe",
            data=b'{"id":"evt_annual_invoice","type":"invoice.paid","data":{"object":{"id":"in_annual"}}}',
            headers={"Stripe-Signature": "test-signature"},
        )
        assert response.status_code == 200
        metrics = c.get("/metrics").get_json()
        assert metrics["confirmed_revenue_usd"] == 1200.0
        assert metrics["mrr_usd"] == 100.0
        assert metrics["one_time_revenue_usd"] == 0.0
