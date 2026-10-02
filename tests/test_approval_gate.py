"""GAR-530: approval_gate unit tests (same module as garcar-enterprise-production). Every refusal path fails closed and is audited."""
import json
import multiprocessing as mp
import os
from datetime import timedelta
from pathlib import Path

import pytest

from approval_gate import ApprovalRequired, gate, require_approval, require_standing_approval
from approval_gate import cli
from gate_support import ROOT, TEST_KEY, gate_env, grant, read_events  # noqa: F401 (fixtures)

EMAIL = "buyer@example.com"


def _reason(fn, *a, **kw):
    with pytest.raises(ApprovalRequired) as ei:
        fn(*a, **kw)
    return ei.value.reason


# ── allow ──

def test_valid_id_allows_and_audits(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL)
    rec = require_approval("send.email", pid, to=EMAIL, site="t")
    assert rec["policy_decision_id"] == pid
    ev = read_events(gate_env)
    assert [e["event_type"] for e in ev] == ["policy.action.allowed.v1"]
    assert ev[0]["policy_decision_id"] == pid
    assert ev[0]["evidence_refs"] == [f"approval:{pid}"]


def test_recipient_match_is_case_insensitive(gate_env, grant):
    pid = grant("--action", "send.email", "--to", "Buyer@Example.com")
    require_approval("send.email", pid, to=" buyer@example.COM ", site="t")


# ── refuse ──

@pytest.mark.parametrize("bad", [None, ""])
def test_missing_id_refused(gate_env, bad):
    assert _reason(require_approval, "send.email", bad, to=EMAIL) == "missing_id"


def test_unknown_id_refused(gate_env):
    assert _reason(require_approval, "send.email", "pd_nope", to=EMAIL) == "unknown_id"


def test_unknown_action_refused(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL)
    assert _reason(require_approval, "send.fax", pid, to=EMAIL) == "unknown_action"


def test_wrong_action_refused(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL)
    assert _reason(require_approval, "send.sms", pid, to=EMAIL) == "wrong_action"


def test_expired_refused(gate_env):
    rec = gate.make_grant(action="send.email", scope={"to": [EMAIL]},
                          expires_in=timedelta(minutes=1), max_uses=1)
    rec["expires_at"] = "2020-01-01T00:00:00Z"
    gate.append_signed(rec, TEST_KEY.encode())
    assert _reason(require_approval, "send.email", rec["policy_decision_id"], to=EMAIL) == "expired"


def test_used_up_refused(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL, "--uses", "2")
    require_approval("send.email", pid, to=EMAIL)
    require_approval("send.email", pid, to=EMAIL)
    assert _reason(require_approval, "send.email", pid, to=EMAIL) == "used_up"


def test_wrong_recipient_refused(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL)
    assert _reason(require_approval, "send.email", pid, to="other@example.com") == "recipient_not_approved"


def test_send_without_recipient_refused(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL)
    assert _reason(require_approval, "send.email", pid) == "missing_to"


def test_over_amount_refused(gate_env, grant):
    pid = grant("--action", "charge.payment_intent", "--max-amount-cents", "5000", "--currency", "usd")
    assert _reason(require_approval, "charge.payment_intent", pid, amount_cents=5001,
                   currency="usd") == "amount_over_cap"
    require_approval("charge.payment_intent", pid, amount_cents=5000, currency="usd")


def test_wrong_currency_refused(gate_env, grant):
    pid = grant("--action", "money.transfer", "--max-amount-cents", "5000", "--currency", "usd")
    assert _reason(require_approval, "money.transfer", pid, amount_cents=10, currency="eur") == "currency_mismatch"


@pytest.mark.parametrize("amount", [0, -5, True, 1.5, "100"])
def test_bad_amount_refused(gate_env, grant, amount):
    pid = grant("--action", "money.transfer", "--max-amount-cents", "5000")
    assert _reason(require_approval, "money.transfer", pid, amount_cents=amount) == "bad_amount"


def test_tampered_signature_refused(gate_env, grant):
    pid = grant("--action", "charge.payment_intent", "--max-amount-cents", "100")
    store = gate_env / "approvals.jsonl"
    rec = json.loads(store.read_text().splitlines()[0])
    rec["scope"]["max_amount_cents"] = 10_000_000  # widen the cap without re-signing
    store.write_text(json.dumps(rec) + "\n")
    assert _reason(require_approval, "charge.payment_intent", pid, amount_cents=500) == "unknown_id"


def test_record_signed_with_other_key_refused(gate_env):
    rec = gate.make_grant(action="send.email", scope={"to": [EMAIL]},
                          expires_in=timedelta(hours=1), max_uses=1)
    gate.append_signed(rec, b"an-attacker-key-" + b"y" * 40)
    assert _reason(require_approval, "send.email", rec["policy_decision_id"], to=EMAIL) == "unknown_id"


def test_missing_store_refused(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL)
    (gate_env / "approvals.jsonl").unlink()
    assert _reason(require_approval, "send.email", pid, to=EMAIL) == "store_unavailable"


@pytest.mark.parametrize("key", [None, "short"])
def test_missing_or_short_verify_key_refused(gate_env, grant, monkeypatch, key):
    pid = grant("--action", "send.email", "--to", EMAIL)
    if key is None:
        monkeypatch.delenv("APPROVAL_VERIFY_KEY")
    else:
        monkeypatch.setenv("APPROVAL_VERIFY_KEY", key)
    assert _reason(require_approval, "send.email", pid, to=EMAIL) == "verify_key_unavailable"


def test_revoked_refused(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL, "--uses", "5")
    assert cli.main(["revoke", "--id", pid]) == 0
    assert _reason(require_approval, "send.email", pid, to=EMAIL) == "revoked"


def test_unwritable_audit_log_refuses_even_valid_id(gate_env, grant, monkeypatch, tmp_path):
    pid = grant("--action", "send.email", "--to", EMAIL)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setenv("APPROVAL_EVENTS_PATH", str(blocker / "events.jsonl"))
    assert _reason(require_approval, "send.email", pid, to=EMAIL) == "audit_unavailable"


def test_no_disable_switch(gate_env, monkeypatch):
    for var in ("APPROVAL_GATE_DISABLED", "APPROVAL_GATE_BYPASS", "SKIP_APPROVAL", "DRY_RUN"):
        monkeypatch.setenv(var, "true")
    assert _reason(require_approval, "send.email", None, to=EMAIL) == "missing_id"


# ── audit events ──

def test_refusal_writes_exactly_one_refused_event_without_pii(gate_env):
    _reason(require_approval, "send.email", "pd_unknown", to=EMAIL, site="unit")
    ev = read_events(gate_env)
    assert len(ev) == 1
    e = ev[0]
    assert e["event_type"] == "policy.action.refused.v1"
    assert e["payload"]["reason"] == "unknown_id"
    assert e["payload"]["target_hash"] == gate.target_hash(EMAIL)
    assert EMAIL not in (gate_env / "policy_events.jsonl").read_text()


def test_events_match_mars_envelope_v11(gate_env, grant):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "tests/fixtures/event-envelope.v1.1.schema.json").read_text())
    pid = grant("--action", "send.email", "--to", EMAIL)
    require_approval("send.email", pid, to=EMAIL)
    _reason(require_approval, "send.email", None, to=EMAIL)
    events = read_events(gate_env)
    assert len(events) == 2
    for e in events:
        jsonschema.validate(e, schema, format_checker=jsonschema.FormatChecker())
        assert e["schema_version"] == "v1.1"
    assert events[1]["policy_decision_id"] is None


# ── standing approvals (customer-initiated checkout only) ──

def test_standing_checkout_approval_allows_unlimited_uses(gate_env, grant):
    pid = grant("--action", "charge.checkout", "--standing", "--plan", "starter",
                "--max-amount-cents", "4900", "--currency", "usd")
    for _ in range(25):
        rec = require_standing_approval("charge.checkout", plan="starter", amount_cents=4900, currency="usd")
        assert rec["policy_decision_id"] == pid
    assert not (gate_env / "approvals.used.jsonl").exists()


def test_standing_is_per_plan_and_price_capped(gate_env, grant):
    grant("--action", "charge.checkout", "--standing", "--plan", "starter", "--max-amount-cents", "4900")
    assert _reason(require_standing_approval, "charge.checkout", plan="enterprise",
                   amount_cents=49900) == "plan_not_approved"
    assert _reason(require_standing_approval, "charge.checkout", plan="starter",
                   amount_cents=9900) == "amount_over_cap"


def test_no_standing_approval_refuses_checkout(gate_env):
    assert _reason(require_standing_approval, "charge.checkout", plan="starter") == "no_standing_approval"


def test_standing_revoked_refuses_checkout(gate_env, grant):
    pid = grant("--action", "charge.checkout", "--standing", "--plan", "starter", "--max-amount-cents", "4900")
    cli.main(["revoke", "--id", pid])
    assert _reason(require_standing_approval, "charge.checkout", plan="starter", amount_cents=4900) == "revoked"


@pytest.mark.parametrize("action", ["send.email", "dial.call", "charge.payment_intent", "money.transfer"])
def test_standing_never_allowed_for_sends_calls_invoices_transfers(gate_env, action):
    with pytest.raises(ValueError):
        gate.make_grant(action=action, scope={"to": [EMAIL], "max_amount_cents": 1},
                        expires_in=timedelta(days=1), max_uses=None, standing=True)
    assert _reason(require_standing_approval, action, plan="starter") == "standing_not_allowed"
    # a hand-built "standing" record for a send is still refused, even if validly signed
    rec = gate.make_grant(action=action, scope={"to": [EMAIL], "max_amount_cents": 100},
                          expires_in=timedelta(days=1), max_uses=1)
    rec["standing"], rec["max_uses"] = True, None
    gate.append_signed(rec, TEST_KEY.encode())
    assert _reason(require_approval, action, rec["policy_decision_id"], to=EMAIL,
                   amount_cents=50) == "standing_not_allowed"


# ── CLI ──

def test_cli_refuses_grants_the_gate_would_refuse(gate_env):
    with pytest.raises(SystemExit):
        cli.main(["grant", "--action", "send.email"])  # no --to
    with pytest.raises(SystemExit):
        cli.main(["grant", "--action", "money.transfer"])  # no cap
    with pytest.raises(SystemExit):
        cli.main(["grant", "--action", "send.email", "--to", EMAIL, "--standing"])
    with pytest.raises(SystemExit):
        cli.main(["grant", "--action", "charge.checkout", "--standing", "--plan", "starter"])  # no cap


def test_cli_needs_signing_key_and_never_prints_it(gate_env, monkeypatch, capsys):
    cli.main(["grant", "--action", "send.email", "--to", EMAIL, "--print-record"])
    out = capsys.readouterr().out
    assert out.startswith("pd_") and TEST_KEY not in out
    cli.main(["list"])
    assert TEST_KEY not in capsys.readouterr().out
    monkeypatch.delenv("APPROVAL_SIGNING_KEY")
    with pytest.raises(SystemExit):
        cli.main(["grant", "--action", "send.email", "--to", EMAIL])


def test_cli_list_shows_use_counts(gate_env, grant, capsys):
    pid = grant("--action", "send.email", "--to", EMAIL, "--uses", "3")
    require_approval("send.email", pid, to=EMAIL)
    capsys.readouterr()
    cli.main(["list"])
    line = capsys.readouterr().out.strip()
    assert pid in line and "uses=1/3" in line and "active" in line


# ── concurrency ──

def _race(pid, q):
    try:
        require_approval("send.email", pid, to=EMAIL, site="race")
        q.put("allowed")
    except ApprovalRequired as exc:
        q.put(exc.reason)


def test_concurrent_uses_of_single_use_id_only_one_passes(gate_env, grant):
    pid = grant("--action", "send.email", "--to", EMAIL, "--uses", "1")
    ctx = mp.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=_race, args=(pid, q)) for _ in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    results = sorted(q.get(timeout=5) for _ in procs)
    assert results.count("allowed") == 1
    assert results.count("used_up") == 7


# ── packaging ──

def test_gate_is_stdlib_only():
    import ast

    stdlib = {"__future__", "hashlib", "hmac", "json", "os", "secrets", "uuid", "datetime",
              "pathlib", "typing", "fcntl", "argparse", "sys"}
    for name in ("gate.py", "cli.py"):
        tree = ast.parse((ROOT / "approval_gate" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [node.module.split(".")[0]]
            else:
                continue
            assert set(mods) <= stdlib, (name, mods)


def test_events_name_this_producer(gate_env):
    _reason(require_approval, "send.contract", None, to=EMAIL)
    assert read_events(gate_env)[0]["producer"] == "apex-revenue-system"
