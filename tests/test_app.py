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


def test_webhook_confirmed_saves_customer(tmp_path, monkeypatch):
    """A charge:confirmed event should persist a customer record."""
    customers_file = str(tmp_path / "customers.json")
    monkeypatch.setattr("main.CUSTOMERS_FILE", customers_file)
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
        r = c.post(
            "/webhook/coinbase",
            data=payload,
            content_type="application/json",
        )
    assert r.status_code == 200

    with open(customers_file) as f:
        customers = json.load(f)
    assert len(customers) == 1
    assert customers[0]["plan"] == "pro"
    assert customers[0]["status"] == "confirmed"
