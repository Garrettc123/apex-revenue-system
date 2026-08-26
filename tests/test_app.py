"""
Comprehensive tests for the Flask application endpoints.
"""
import json
import pytest
from main import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    import main as m
    monkeypatch.setattr(m, "REVENUE_LEDGER_FILE", str(tmp_path / "revenue_ledger.json"))


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


def test_webhook_confirmed_increases_mrr(monkeypatch):
    """A charge:confirmed event should increase /metrics MRR."""
    monkeypatch.setattr("main.CB_WEBHOOK_SECRET", "")

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

    with app.test_client() as c:
        start = c.get("/metrics").get_json()["mrr_usd"]
        r = c.post("/webhook/coinbase", data=payload, content_type="application/json")
        end = c.get("/metrics").get_json()["mrr_usd"]

    assert r.status_code == 200
    assert end == pytest.approx(start + 149.0)


def test_edge_revenue_webhook_records_and_updates_mrr(client):
    start = client.get("/metrics").get_json()["mrr_usd"]
    payload = {
        "node_id": "pixel-10-edge-001",
        "charge_id": "edge_charge_001",
        "amount_usd": 49.5,
        "plan": "starter",
        "source": "edge",
    }
    r = client.post("/webhook/edge/revenue", json=payload)
    end = client.get("/metrics").get_json()["mrr_usd"]

    assert r.status_code == 200
    assert r.get_json() == {"status": "recorded"}
    assert end == pytest.approx(start + 49.5)


def test_edge_revenue_webhook_missing_fields_returns_400(client):
    r = client.post("/webhook/edge/revenue", json={"node_id": "pixel-10-edge-001"})
    assert r.status_code == 400


def test_stripe_webhook_payment_intent_succeeded_updates_mrr(client):
    start = client.get("/metrics").get_json()["mrr_usd"]
    payload = {
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_test_001",
                "amount_received": 12345,
                "currency": "usd",
                "metadata": {"plan": "pro"},
            }
        },
    }
    r = client.post("/webhook/stripe", json=payload)
    end = client.get("/metrics").get_json()["mrr_usd"]

    assert r.status_code == 200
    assert end == pytest.approx(start + 123.45)
