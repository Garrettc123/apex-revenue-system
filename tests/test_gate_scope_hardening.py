"""GAR-530: a scoped approval never covers a request that leaves the scoped field out.

If Garrett caps a standing approval (max_amount_cents) or pins its currency, a checkout
that omits the amount or currency must be refused by BOTH the Python and the JS gate.
"""
import json
import os
import shutil
import subprocess

import pytest

from approval_gate import ApprovalRequired, require_approval, require_standing_approval
from gate_support import ROOT, gate_env, grant  # noqa: F401 (fixtures)

NODE = shutil.which("node")
GATE_JS = ROOT / "src" / "lib" / "approvalGate.js"
CHECK = r"""
const g = require(process.argv[1]);
try {
  const rec = g.requireStandingApproval('charge.checkout', JSON.parse(process.argv[2]));
  console.log(JSON.stringify({ ok: true, id: rec.policy_decision_id }));
} catch (e) {
  if (!(e instanceof g.ApprovalRequired)) throw e;
  console.log(JSON.stringify({ ok: false, reason: e.reason }));
}
"""


def _reason(fn, *a, **kw):
    with pytest.raises(ApprovalRequired) as ei:
        fn(*a, **kw)
    return ei.value.reason


def _standing(grant):
    return grant("--action", "charge.checkout", "--standing", "--plan", "web/starter",
                 "--max-amount-cents", "29900", "--currency", "usd")


def test_python_capped_standing_refuses_unstated_amount_or_currency(gate_env, grant):
    _standing(grant)
    assert _reason(require_standing_approval, "charge.checkout", plan="web/starter",
                   currency="usd") == "missing_amount_cents"
    assert _reason(require_standing_approval, "charge.checkout", plan="web/starter",
                   amount_cents=29900) == "missing_currency"
    require_standing_approval("charge.checkout", plan="web/starter", amount_cents=29900, currency="USD")


def test_python_currency_scoped_transfer_refuses_unstated_currency(gate_env, grant):
    pid = grant("--action", "money.transfer", "--max-amount-cents", "5000", "--currency", "usd")
    assert _reason(require_approval, "money.transfer", pid, amount_cents=10) == "missing_currency"
    require_approval("money.transfer", pid, amount_cents=10, currency="usd")


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("opts,expected", [
    ({"plan": "web/starter", "currency": "usd"}, {"ok": False, "reason": "missing_amount_cents"}),
    ({"plan": "web/starter", "amountCents": 29900}, {"ok": False, "reason": "missing_currency"}),
    ({"plan": "web/starter", "amountCents": 29900, "currency": ""}, {"ok": False, "reason": "missing_currency"}),
])
def test_js_capped_standing_refuses_unstated_amount_or_currency(gate_env, grant, opts, expected):
    _standing(grant)
    out = subprocess.run([NODE, "-e", CHECK, str(GATE_JS), json.dumps({**opts, "site": "pytest"})],
                         capture_output=True, text=True, timeout=30, env=os.environ.copy(), check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == expected


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_capped_standing_allows_full_request(gate_env, grant):
    pid = _standing(grant)
    opts = {"plan": "web/starter", "amountCents": 29900, "currency": "usd", "site": "pytest"}
    out = subprocess.run([NODE, "-e", CHECK, str(GATE_JS), json.dumps(opts)],
                         capture_output=True, text=True, timeout=30, env=os.environ.copy(), check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"ok": True, "id": pid}
