"""GAR-530: every apex charge/send site refuses before the network call.

The network primitive (requests.post via ``main.http``, ``stripe.checkout.Session``) is
replaced with a recorder. Without an approval the recorder never fires; with a valid
approval it fires exactly once per action.
"""
import json
import sys
import types

import pytest
from flask import Flask

import main
from approval_gate import ApprovalRequired
from gate_support import gate_env, grant, read_events  # noqa: F401 (fixtures)

EMAIL = "buyer@example.com"


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class Recorder:
    def __init__(self, data):
        self.calls = []
        self.data = data

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        return _Resp(self.data)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REVENUE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


# ── A1 main.py checkout(plan): Coinbase Commerce /charges (standing approval) ──

@pytest.fixture
def coinbase(monkeypatch):
    rec = Recorder({"data": {"hosted_url": "https://commerce.example/pay/abc"}})
    monkeypatch.setattr(main, "CB_API_KEY", "test-cb-key")
    monkeypatch.setattr(main.http, "post", rec)
    return rec


def test_a1_coinbase_checkout_refused_without_standing_approval(gate_env, client, coinbase):
    r = client.get("/checkout/pro")
    assert r.status_code == 403
    assert r.get_json()["reason"] == "no_standing_approval"
    assert coinbase.calls == []
    assert read_events(gate_env)[0]["payload"]["site"] == "apex.main.checkout"


def test_a1_standing_plan_approval_keeps_pay_button_working(gate_env, grant, client, coinbase):
    grant("--action", "charge.checkout", "--standing", "--plan", "coinbase/pro",
          "--max-amount-cents", "14900", "--currency", "usd")
    for _ in range(3):
        r = client.get("/checkout/pro")
        assert r.status_code == 303
    assert len(coinbase.calls) == 3
    assert coinbase.calls[0][0].endswith("/charges")
    # the pro approval does not open the enterprise plan
    assert client.get("/checkout/enterprise").status_code == 403
    assert len(coinbase.calls) == 3


def test_a1_other_surface_approval_does_not_cover_coinbase(gate_env, grant, client, coinbase):
    grant("--action", "charge.checkout", "--standing", "--plan", "web/pro", "--max-amount-cents", "999999")
    assert client.get("/checkout/pro").status_code == 403
    assert coinbase.calls == []


def test_a1_price_cap_enforced(gate_env, grant, client, coinbase):
    grant("--action", "charge.checkout", "--standing", "--plan", "coinbase/pro", "--max-amount-cents", "100")
    r = client.get("/checkout/pro")
    assert r.status_code == 403 and r.get_json()["reason"] == "amount_over_cap"
    assert coinbase.calls == []


# ── A2 core/payments.py create_checkout_session: Stripe subscription (standing approval) ──

class _FakeSession:
    calls = []

    @classmethod
    def create(cls, **kw):
        cls.calls.append(kw)
        return {"id": "cs_test_1", "url": "https://checkout.example/cs_test_1"}


@pytest.fixture
def fake_stripe(monkeypatch):
    _FakeSession.calls = []
    mod = types.ModuleType("stripe")
    mod.checkout = types.SimpleNamespace(Session=_FakeSession)
    monkeypatch.setitem(sys.modules, "stripe", mod)
    return _FakeSession


def _create(tier="audit"):
    from core import payments

    return payments.create_checkout_session("cust_1", tier, "https://apex.example",
                                            {"audit": "price_a", "sprint": "price_s"}, "sk_test_dummy")


def test_a2_stripe_checkout_refused_without_standing_approval(gate_env, fake_stripe):
    with pytest.raises(ApprovalRequired) as ei:
        _create()
    assert ei.value.reason == "no_standing_approval"
    assert fake_stripe.calls == []


def test_a2_stripe_checkout_allowed_per_tier(gate_env, grant, fake_stripe):
    grant("--action", "charge.checkout", "--standing", "--plan", "stripe/audit",
          "--max-amount-cents", "4700", "--currency", "usd")
    assert _create("audit")["id"] == "cs_test_1"
    assert _create("starter")["tier"] == "audit"  # legacy alias maps to the same approved tier
    assert len(fake_stripe.calls) == 2
    with pytest.raises(ApprovalRequired):
        _create("sprint")
    assert len(fake_stripe.calls) == 2


def test_a2_api_checkout_returns_403(gate_env, fake_stripe, monkeypatch, tmp_path):
    from core import phase12_api

    monkeypatch.setenv("REVENUE_DB_FILE", str(tmp_path / "rev.db"))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_ID_AUDIT", "price_a")
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    app = Flask("t")
    phase12_api.register_phase12_routes(app)
    r = app.test_client().post("/api/checkout", json={"customer_id": "c1", "tier": "audit"})
    assert r.status_code == 403
    assert r.get_json()["reason"] == "no_standing_approval"
    assert fake_stripe.calls == []


# ── A4 main.py _docusign_send_contract: DocuSign envelope (per-action approval) ──

@pytest.fixture
def docusign(monkeypatch):
    rec = Recorder({"envelopeId": "env_1"})
    monkeypatch.setattr(main, "DOCUSIGN_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(main, "DOCUSIGN_ACCOUNT_ID", "acct")
    monkeypatch.setattr(main, "DOCUSIGN_TEMPLATE_ID", "tmpl")
    monkeypatch.setattr(main.http, "post", rec)
    return rec


def _order(oid="7001"):
    return {"id": oid, "email": EMAIL, "customer": {"first_name": "Jane", "last_name": "Doe", "email": EMAIL},
            "total_price": "297.00", "line_items": [{"title": "IRAS"}]}


def test_a4_shopify_webhook_no_longer_auto_sends_contract(gate_env, client, docusign):
    r = client.post("/webhook/shopify", data=json.dumps(_order()),
                    headers={"X-Shopify-Topic": "orders/paid", "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.get_json()["integrations"]["docusign"].startswith("refused")
    assert docusign.calls == []
    ev = read_events(gate_env)
    assert ev[-1]["payload"]["reason"] == "missing_id" and ev[-1]["correlation_id"] == "7001"
    assert EMAIL not in (gate_env / "policy_events.jsonl").read_text()


def test_a4_contract_sends_once_with_approval(gate_env, grant, client, docusign):
    pid = grant("--action", "send.contract", "--to", EMAIL, "--uses", "1")
    body = {"order_id": "7002", "email": EMAIL, "name": "Jane Doe", "approval_id": pid}
    r = client.post("/contracts/send", json=body)
    assert r.status_code == 200 and r.get_json()["status"] == "sent"
    assert len(docusign.calls) == 1
    assert docusign.calls[0][1]["json"]["templateRoles"][0]["email"] == EMAIL
    assert client.post("/contracts/send", json=body).status_code == 403  # used up
    assert len(docusign.calls) == 1


def test_a4_contract_approval_is_recipient_scoped(gate_env, grant, client, docusign):
    pid = grant("--action", "send.contract", "--to", EMAIL)
    r = client.post("/contracts/send", json={"email": "someone-else@example.com", "approval_id": pid})
    assert r.status_code == 403
    assert docusign.calls == []


def test_a4_contract_send_without_id_refused(gate_env, client, docusign):
    assert client.post("/contracts/send", json={"email": EMAIL}).status_code == 403
    assert docusign.calls == []
