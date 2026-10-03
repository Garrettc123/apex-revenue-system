"""Garrett's approval CLI (GAR-530).

    export APPROVAL_SIGNING_KEY=...   # from your vault; never commit or paste it
    export APPROVALS_PATH=/secure/path/approvals.jsonl

    # per-action: one email to one recipient, valid 24h, one use
    python -m approval_gate.cli grant --action send.email --to buyer@example.com --expires 24h --uses 1

    # per-action: one DocuSign contract to one signer
    python -m approval_gate.cli grant --action send.contract --to buyer@example.com --expires 7d

    # standing approval for self-serve checkout of one plan (buyer clicks Pay)
    python -m approval_gate.cli grant --action charge.checkout --standing --plan coinbase/starter \
        --max-amount-cents 4900 --currency usd --expires 90d

    python -m approval_gate.cli revoke --id pd_...
    python -m approval_gate.cli list

`grant` prints only the new policy_decision_id (add --print-record to also print the
signed record line, e.g. for the Vercel APPROVALS_JSONL env var). The key is
never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import gate


def _signing_key() -> bytes:
    key = gate._key("APPROVAL_SIGNING_KEY")
    if key is None:
        raise SystemExit(f"APPROVAL_SIGNING_KEY is not set (or shorter than {gate.MIN_KEY_LEN} chars)")
    return key


def cmd_grant(a: argparse.Namespace) -> int:
    scope = {}
    if a.to:
        scope["to"] = [t.strip() for t in a.to]
    if a.max_amount_cents is not None:
        scope["max_amount_cents"] = a.max_amount_cents
    if a.currency:
        scope["currency"] = a.currency.lower()
    if a.plan:
        scope["plan"] = a.plan
    if a.standing and ("plan" not in scope or "max_amount_cents" not in scope):
        raise SystemExit("standing checkout approvals need --plan and --max-amount-cents")
    for need in gate.REQUIRED_SCOPE.get(a.action, ()):  # refuse grants the gate would refuse
        field = "max_amount_cents" if need == "amount_cents" else need
        if field not in scope:
            raise SystemExit(f"{a.action} approvals need --{field.replace('_', '-')}")
    expires = a.expires or ("90d" if a.standing else "24h")
    rec = gate.make_grant(action=a.action, scope=scope, expires_in=gate.parse_duration(expires),
                          max_uses=None if a.standing else a.uses, standing=a.standing,
                          approver=a.approver, tenant_id=a.tenant_id, note=a.note)
    signed = gate.append_signed(rec, _signing_key())
    print(signed["policy_decision_id"])
    if a.print_record:
        print(json.dumps(signed, sort_keys=True))
    return 0


def cmd_revoke(a: argparse.Namespace) -> int:
    rec = {"type": "revoke", "policy_decision_id": a.id,
           "revoked_at": gate._iso(gate._now()), "note": a.note}
    gate.append_signed(rec, _signing_key())
    print(f"revoked {a.id}")
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    key = gate._key("APPROVAL_SIGNING_KEY") or gate._key("APPROVAL_VERIFY_KEY")
    if key is None:
        raise SystemExit("set APPROVAL_SIGNING_KEY or APPROVAL_VERIFY_KEY to verify records")
    store = gate.load_records(key)
    for pid, rec in store["grants"].items():
        uses = gate._count_uses(pid)
        cap = "unlimited" if rec.get("standing") else rec.get("max_uses")
        state = "revoked" if pid in store["revoked"] else "active"
        if state == "active" and (gate._parse(rec.get("expires_at")) or gate._now()) <= gate._now():
            state = "expired"
        print(f"{pid}\t{rec.get('action')}\t{state}\tuses={uses}/{cap}\texpires={rec.get('expires_at')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m approval_gate.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grant", help="issue a signed approval")
    g.add_argument("--action", required=True, choices=sorted(gate.ACTIONS))
    g.add_argument("--to", action="append", help="approved recipient (repeatable)")
    g.add_argument("--max-amount-cents", type=int)
    g.add_argument("--currency")
    g.add_argument("--plan")
    g.add_argument("--expires", help="e.g. 30m, 24h, 7d (default 24h; 90d for --standing)")
    g.add_argument("--uses", type=int, default=1)
    g.add_argument("--standing", action="store_true", help="charge.checkout only: unlimited uses per plan")
    g.add_argument("--approver", default="garrett")
    g.add_argument("--tenant-id", default="garcar")
    g.add_argument("--note", default="")
    g.add_argument("--print-record", action="store_true")
    g.set_defaults(fn=cmd_grant)

    r = sub.add_parser("revoke", help="revoke an approval")
    r.add_argument("--id", required=True)
    r.add_argument("--note", default="")
    r.set_defaults(fn=cmd_revoke)

    ls = sub.add_parser("list", help="list approvals and use counts")
    ls.set_defaults(fn=cmd_list)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except ValueError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    sys.exit(main())
