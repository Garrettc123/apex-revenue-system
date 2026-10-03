"""Hardening audit (Oct 2026): internal and AI endpoints need APEX_API_KEY.

- 503 when APEX_API_KEY is not configured (fail closed)
- 401 for a missing or wrong key (X-API-Key or Authorization: Bearer)
- normal behaviour with the right key
- 429 once a key/IP goes over APEX_RATE_LIMIT_PER_MIN in a minute
All values are dummies.
"""
import pytest
from flask.testing import FlaskClient

import api_auth
from main import app

KEY = "unit-test-apex-key-not-real"  # same dummy as tests/conftest.py

PROTECTED = [
    ("GET", "/metrics"),
    ("GET", "/integrations/status"),
    ("GET", "/genesis"),
    ("POST", "/genesis"),
    ("GET", "/ai/leads"),
    ("POST", "/ai/analyze"),
]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr("main.REVENUE_LEDGER_FILE", str(tmp_path / "revenue_ledger.json"))
    monkeypatch.setattr("main._gemini_client", None)  # never call a real model
    monkeypatch.setenv("APEX_API_KEY", KEY)
    monkeypatch.delenv("APEX_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.setattr(app, "test_client_class", FlaskClient)  # no default key header
    api_auth.reset_rate_limits()
    yield
    api_auth.reset_rate_limits()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _call(client, method, path, headers=None):
    if method == "POST":
        return client.post(path, json={"prompt": "x"}, headers=headers or {})
    return client.get(path, headers=headers or {})


@pytest.mark.parametrize("method,path", PROTECTED)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_503_when_key_not_configured(client, monkeypatch, method, path, value):
    if value is None:
        monkeypatch.delenv("APEX_API_KEY", raising=False)
    else:
        monkeypatch.setenv("APEX_API_KEY", value)
    r = _call(client, method, path, {"X-API-Key": KEY})
    assert r.status_code == 503
    assert "APEX_API_KEY" in r.get_json()["error"]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_401_without_key(client, method, path):
    r = _call(client, method, path)
    assert r.status_code == 401


@pytest.mark.parametrize("method,path", PROTECTED)
def test_401_with_wrong_key(client, method, path):
    assert _call(client, method, path, {"X-API-Key": "wrong"}).status_code == 401
    assert _call(client, method, path, {"Authorization": "Bearer wrong"}).status_code == 401


def test_metrics_and_status_200_with_key(client):
    assert client.get("/metrics", headers={"X-API-Key": KEY}).status_code == 200
    assert client.get("/integrations/status", headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


@pytest.mark.parametrize("method,path", [p for p in PROTECTED if p[1] in ("/genesis", "/ai/leads", "/ai/analyze")])
def test_ai_endpoints_pass_auth_then_report_gemini_missing(client, method, path):
    # With the right key the request reaches the handler, which still 503s on no GEMINI_API_KEY.
    r = _call(client, method, path, {"X-API-Key": KEY})
    assert r.status_code == 503
    assert "GEMINI_API_KEY" in r.get_json()["error"]


def test_rate_limit_per_key_and_ip(client, monkeypatch):
    monkeypatch.setenv("APEX_RATE_LIMIT_PER_MIN", "3")
    codes = [client.get("/metrics", headers={"X-API-Key": KEY}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]
    r = client.get("/metrics", headers={"X-API-Key": KEY})
    assert r.headers.get("Retry-After")


def test_rate_limit_is_per_ip(client, monkeypatch):
    monkeypatch.setenv("APEX_RATE_LIMIT_PER_MIN", "2")
    h = {"X-API-Key": KEY}
    for _ in range(2):
        assert client.get("/metrics", headers=h).status_code == 200
    assert client.get("/metrics", headers=h).status_code == 429
    other = client.get("/metrics", headers=h, environ_base={"REMOTE_ADDR": "10.9.9.9"})
    assert other.status_code == 200


def test_failed_auth_attempts_are_rate_limited(client, monkeypatch):
    monkeypatch.setenv("APEX_RATE_LIMIT_PER_MIN", "2")
    codes = [client.get("/metrics", headers={"X-API-Key": f"guess{i}"}).status_code for i in range(4)]
    assert codes == [401, 401, 429, 429]


def test_public_endpoints_stay_open(client):
    assert client.get("/health").status_code == 200
