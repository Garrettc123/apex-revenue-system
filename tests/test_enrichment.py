import pytest

import main as m
from core import payments


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "revenue_engine.db")
    monkeypatch.setattr(m, "REVENUE_DB_FILE", db_path)
    payments.init_db(db_path)


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    with m.app.test_client() as c:
        yield c


def test_enrich_endpoint_returns_enrichment_and_persists(client):
    r = client.post(
        "/api/enrich",
        json={
            "customer_id": "free_1",
            "lead": {
                "full_name": "Ada Lovelace",
                "email": "ada@example.com",
                "company_name": "Analytical Engines",
                "company_size": 120,
            },
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["result"]["enrichment_status"] == "complete"
    assert data["enrichment_id"] > 0


def test_enrich_rate_limit_gate(client):
    payments.provision_customer(m.REVENUE_DB_FILE, "free_2", tier="free", status="active")
    for i in range(50):
        payments.record_enrichment(
            m.REVENUE_DB_FILE,
            "free_2",
            {"email": f"u{i}@example.com"},
            {"ok": True},
        )

    r = client.post("/api/enrich", json={"customer_id": "free_2", "lead": {"email": "overflow@example.com"}})
    assert r.status_code == 402


def test_bulk_enrichment_requires_paid_tier(client):
    payments.provision_customer(m.REVENUE_DB_FILE, "free_3", tier="free", status="active")
    r = client.post(
        "/api/enrich/bulk",
        json={"customer_id": "free_3", "leads": [{"email": "a@example.com"}]},
    )
    assert r.status_code == 403


def test_bulk_enrichment_starter_allows_batch(client):
    payments.set_subscription(m.REVENUE_DB_FILE, "starter_1", tier="starter", active=True, status="active")
    r = client.post(
        "/api/enrich/bulk",
        json={
            "customer_id": "starter_1",
            "leads": [
                {"full_name": "One", "email": "one@example.com", "company_name": "A"},
                {"full_name": "Two", "email": "two@example.com", "company_name": "B"},
            ],
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 2
