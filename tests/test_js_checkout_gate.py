"""GAR-530: the JS gate in src/lib/approvalGate.js and the Next.js checkout route.

Approvals are issued with the *Python* CLI and verified by the *JS* gate, so these tests
also prove the two canonical-JSON/HMAC implementations agree. Needs `node` (>= 18).
"""
import json
import os
import shutil
import subprocess
from datetime import timedelta

import pytest

from approval_gate import cli, gate
from gate_support import ROOT, TEST_KEY, gate_env, grant, read_events  # noqa: F401 (fixtures)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
GATE_JS = ROOT / "src" / "lib" / "approvalGate.js"

CHECK = r"""
const g = require(process.argv[1]);
try {
  const rec = g.requireStandingApproval(process.argv[2], JSON.parse(process.argv[3]));
  console.log(JSON.stringify({ ok: true, id: rec.policy_decision_id }));
} catch (e) {
  if (!(e instanceof g.ApprovalRequired)) throw e;
  console.log(JSON.stringify({ ok: false, reason: e.reason }));
}
"""


def js_check(plan, amount=None, action="charge.checkout", env=None):
    opts = {"plan": plan, "currency": "usd", "site": "pytest"}
    if amount is not None:
        opts["amountCents"] = amount
    out = subprocess.run([NODE, "-e", CHECK, str(GATE_JS), action, json.dumps(opts)],
                         capture_output=True, text=True, timeout=30, env=env or os.environ.copy(), check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def _standing(grant, plan="web/starter", cap="29900", note=""):
    return grant("--action", "charge.checkout", "--standing", "--plan", plan,
                 "--max-amount-cents", cap, "--currency", "usd", "--note", note)


def test_js_accepts_python_signed_standing_approval(gate_env, grant):
    # non-ASCII note exercises the ensure_ascii part of the canonical form
    pid = _standing(grant, note="café ✓ — standing")
    assert js_check("web/starter", 29900) == {"ok": True, "id": pid}
    ev = read_events(gate_env)
    assert ev[-1]["event_type"] == "policy.action.allowed.v1"
    assert ev[-1]["policy_decision_id"] == pid and ev[-1]["producer"] == "apex-revenue-system"


@pytest.mark.parametrize("plan,amount,reason", [
    ("web/enterprise", 199900, "plan_not_approved"),
    ("web/starter", 34900, "amount_over_cap"),
    ("web/starter", 0, "bad_amount"),
])
def test_js_scope_refusals(gate_env, grant, plan, amount, reason):
    _standing(grant)
    assert js_check(plan, amount) == {"ok": False, "reason": reason}


def test_js_no_approval_refused(gate_env):
    assert js_check("web/starter", 29900) == {"ok": False, "reason": "no_standing_approval"}


def test_js_tampered_record_refused(gate_env, grant):
    _standing(grant, cap="100")
    store = gate_env / "approvals.jsonl"
    rec = json.loads(store.read_text().splitlines()[0])
    rec["scope"]["max_amount_cents"] = 10_000_000
    store.write_text(json.dumps(rec) + "\n")
    assert js_check("web/starter", 29900) == {"ok": False, "reason": "no_standing_approval"}


def test_js_revoked_and_expired_refused(gate_env, grant):
    pid = _standing(grant)
    cli.main(["revoke", "--id", pid])
    assert js_check("web/starter", 29900)["reason"] == "revoked"
    rec = gate.make_grant(action="charge.checkout", scope={"plan": "web/pro", "max_amount_cents": 79900},
                          expires_in=timedelta(days=1), max_uses=None, standing=True)
    rec["expires_at"] = "2020-01-01T00:00:00Z"
    gate.append_signed(rec, TEST_KEY.encode())
    assert js_check("web/pro", 79900)["reason"] == "expired"


def test_js_per_action_record_is_not_a_standing_approval(gate_env, grant):
    grant("--action", "charge.checkout", "--plan", "web/starter", "--max-amount-cents", "29900")
    assert js_check("web/starter", 29900) == {"ok": False, "reason": "no_standing_approval"}


def test_js_only_checkout_can_be_standing(gate_env, grant):
    _standing(grant)
    assert js_check("web/starter", 29900, action="send.email")["reason"] == "standing_not_allowed"


@pytest.mark.parametrize("problem", ["no_key", "short_key", "no_store", "audit_unwritable"])
def test_js_fails_closed(gate_env, grant, tmp_path, problem):
    _standing(grant)
    env = os.environ.copy()
    if problem == "no_key":
        env.pop("APPROVAL_VERIFY_KEY")
    elif problem == "short_key":
        env["APPROVAL_VERIFY_KEY"] = "short"
    elif problem == "no_store":
        env["APPROVALS_PATH"] = str(tmp_path / "missing.jsonl")
    else:
        blocker = tmp_path / "file"
        blocker.write_text("")
        env["APPROVAL_EVENTS_PATH"] = str(blocker / "events.jsonl")
    want = {"no_key": "verify_key_unavailable", "short_key": "verify_key_unavailable",
            "no_store": "store_unavailable", "audit_unwritable": "audit_unavailable"}[problem]
    assert js_check("web/starter", 29900, env=env) == {"ok": False, "reason": want}


def test_js_inline_records_for_serverless(gate_env, grant):
    _standing(grant)
    env = os.environ.copy()
    env["APPROVALS_JSONL"] = (gate_env / "approvals.jsonl").read_text()
    env.pop("APPROVALS_PATH")
    env.pop("APPROVAL_EVENTS_PATH")
    out = subprocess.run([NODE, "-e", CHECK, str(GATE_JS), "charge.checkout",
                          json.dumps({"plan": "web/starter", "amountCents": 29900, "currency": "usd"})],
                         capture_output=True, text=True, timeout=30, env=env, check=True, cwd=str(gate_env))
    lines = out.stdout.strip().splitlines()
    assert json.loads(lines[-1])["ok"] is True
    event = json.loads(lines[0])  # no events path -> one JSON event line on stdout
    assert event["event_type"] == "policy.action.allowed.v1"


def test_js_events_match_mars_envelope_v11(gate_env, grant):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "tests/fixtures/event-envelope.v1.1.schema.json").read_text())
    _standing(grant)
    js_check("web/starter", 29900)
    js_check("web/enterprise", 199900)
    events = read_events(gate_env)
    assert [e["event_type"] for e in events] == ["policy.action.allowed.v1", "policy.action.refused.v1"]
    for e in events:
        jsonschema.validate(e, schema, format_checker=jsonschema.FormatChecker())


# ── the Next.js route itself, with a fake `stripe` package ──

HARNESS = r"""
import handlerMod from './src/pages/api/checkout/session.mjs';
const handler = handlerMod.default || handlerMod;
const body = JSON.parse(process.argv[2]);
const method = process.argv[3] || 'POST';
const res = { code: 0, payload: null,
  status(c) { this.code = c; return this; },
  json(p) { this.payload = p; console.log(JSON.stringify({ code: this.code, body: p })); return this; } };
await handler({ method, body }, res);
"""

FAKE_STRIPE = r"""
const fs = require('fs');
class Stripe {
  constructor() {
    this.checkout = { sessions: { create: async (args) => {
      fs.appendFileSync(process.env.FAKE_STRIPE_LOG, JSON.stringify(args) + '\n');
      return { id: 'cs_test_js', url: 'https://checkout.example/cs_test_js' };
    } } };
  }
}
module.exports = Stripe;
module.exports.default = Stripe;
"""


@pytest.fixture
def route(tmp_path):
    app = tmp_path / "app"
    (app / "src/pages/api/checkout").mkdir(parents=True)
    (app / "src/lib").mkdir(parents=True)
    shutil.copy(ROOT / "src/pages/api/checkout/session.js", app / "src/pages/api/checkout/session.mjs")
    shutil.copy(GATE_JS, app / "src/lib/approvalGate.js")
    (app / "node_modules/stripe").mkdir(parents=True)
    (app / "node_modules/stripe/index.js").write_text(FAKE_STRIPE)
    (app / "node_modules/stripe/package.json").write_text('{"name":"stripe","main":"index.js"}')
    (app / "package.json").write_text('{"name":"t","type":"commonjs"}')
    (app / "run.mjs").write_text(HARNESS)
    log = tmp_path / "stripe_calls.jsonl"

    def call(body, method="POST"):
        env = os.environ.copy()
        env["FAKE_STRIPE_LOG"] = str(log)
        env["STRIPE_SECRET_KEY"] = "sk_test_dummy"
        out = subprocess.run([NODE, "run.mjs", json.dumps(body), method], cwd=app, capture_output=True,
                             text=True, timeout=30, env=env, check=True)
        result = json.loads(out.stdout.strip().splitlines()[-1])
        calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return call


def test_route_refuses_without_standing_approval(gate_env, route):
    result, calls = route({"plan": "starter"})
    assert result["code"] == 403 and result["body"]["reason"] == "no_standing_approval"
    assert calls == []


def test_route_creates_session_with_standing_approval(gate_env, grant, route):
    _standing(grant, plan="web/starter", cap="34900")
    result, calls = route({"plan": "starter"})
    assert result["code"] == 200 and result["body"]["sessionId"] == "cs_test_js"
    result, calls = route({"plan": "starter", "data_tier": "volume"})
    assert result["code"] == 200
    assert [c["line_items"][0]["price_data"]["unit_amount"] for c in calls] == [29900, 34900]


def test_route_volume_upcharge_respects_cap(gate_env, grant, route):
    _standing(grant, plan="web/starter", cap="29900")
    result, calls = route({"plan": "starter", "data_tier": "volume"})
    assert result["code"] == 403 and result["body"]["reason"] == "amount_over_cap"
    assert calls == []


def test_route_unknown_plan_and_method(gate_env, route):
    assert route({"plan": "nope"})[0]["code"] == 400
    assert route({}, method="GET")[0]["code"] == 405
