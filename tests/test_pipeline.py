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


def test_pipeline_prospect_crud_and_stats(client):
    payments.set_subscription(m.REVENUE_DB_FILE, "cust_pipeline", tier="starter", active=True, status="active")

    created = client.post(
        "/api/pipeline/prospect",
        json={
            "customer_id": "cust_pipeline",
            "prospect": {
                "name": "Taylor",
                "email": "taylor@example.com",
                "company": "Pipeline Co",
                "company_size": 80,
                "budget_usd": 12000,
                "decision_timeline_days": 20,
            },
        },
    )
    assert created.status_code == 201
    assert created.get_json()["lead"]["score"] > 0

    leads = client.get("/api/pipeline/leads?customer_id=cust_pipeline")
    assert leads.status_code == 200
    assert len(leads.get_json()["leads"]) == 1

    stats = client.get("/api/pipeline/stats?customer_id=cust_pipeline")
    assert stats.status_code == 200
    assert stats.get_json()["total_leads"] == 1


def test_pipeline_access_denied_for_free_tier(client):
    payments.provision_customer(m.REVENUE_DB_FILE, "cust_free", tier="free", status="active")
    r = client.get("/api/pipeline/leads?customer_id=cust_free")
    assert r.status_code == 403


def test_enriched_lead_auto_enters_pipeline(client):
    payments.set_subscription(m.REVENUE_DB_FILE, "cust_auto", tier="starter", active=True, status="active")
    enrich = client.post(
        "/api/enrich",
        json={
            "customer_id": "cust_auto",
            "lead": {
                "full_name": "Auto Lead",
                "email": "auto@example.com",
                "company_name": "Auto Inc",
                "company_size": 300,
                "budget_usd": 15000,
                "decision_timeline_days": 15,
            },
        },
    )
    assert enrich.status_code == 200

    leads = client.get("/api/pipeline/leads?customer_id=cust_auto")
    assert leads.status_code == 200
    assert len(leads.get_json()["leads"]) == 1
