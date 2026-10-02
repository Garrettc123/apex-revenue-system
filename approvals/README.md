# Approvals store (GAR-530)

Nothing in this folder except this README is ever committed (see `.gitignore`).

| File | Env var | Written by | Contents |
|---|---|---|---|
| `approvals.jsonl` | `APPROVALS_PATH` (or `APPROVALS_JSONL` for the JS gate) | **Garrett only**, via the CLI | Signed grant and revoke records |
| `approvals.used.jsonl` | `APPROVALS_USED_PATH` | the gate | One line per consumed use (append-only) |
| `policy_events.jsonl` | `APPROVAL_EVENTS_PATH` (or `EVENTS_PATH`) | the gate | `policy.action.allowed.v1` / `policy.action.refused.v1` events, MARS envelope v1.1 |

Keys (set in your vault / host env, **never** in git, CI logs, or PR text):

- `APPROVAL_SIGNING_KEY`: used by the CLI on Garrett's machine to sign records.
- `APPROVAL_VERIFY_KEY`: used by services to verify records. With HMAC-SHA256 this is the
  same value as the signing key, so anything holding it could mint approvals. Hardening
  follow-up: Ed25519 (services get only the public key).

Both must be at least 32 characters. Generate one locally with
`python -c "import secrets; print(secrets.token_urlsafe(48))"` and store it straight in
your vault.

## Issuing approvals

Standing checkout approvals are **per surface and plan**, because the three checkout surfaces
use the same plan names at different prices:

| Surface | Code | `--plan` values | Price in code (cents) |
|---|---|---|---|
| Coinbase Commerce (`/checkout/<plan>`) | `main.py` `checkout` | `coinbase/starter`, `coinbase/pro`, `coinbase/enterprise` | 4900 / 14900 / 49900 |
| Stripe subscription (`POST /api/checkout`) | `core/payments.py` `create_checkout_session` | `stripe/audit`, `stripe/sprint`, `stripe/retainer` | 4700 / 49700 / 149700 (from `config/tiers.py`) |
| Next.js (`POST /api/checkout/session`) | `src/pages/api/checkout/session.js` | `web/starter`, `web/professional`, `web/enterprise` | 29900 / 79900 / 199900, **+5000** with `data_tier=volume` |

```bash
# STANDING approval: self-serve checkout for one surface+plan keeps the Pay button working
python -m approval_gate.cli grant --action charge.checkout --standing --plan coinbase/starter --max-amount-cents 4900 --currency usd --expires 90d
python -m approval_gate.cli grant --action charge.checkout --standing --plan web/starter --max-amount-cents 34900 --currency usd --expires 90d --print-record

# one DocuSign contract to one signer (then POST /contracts/send with the approval_id)
python -m approval_gate.cli grant --action send.contract --to buyer@example.com --expires 7d --uses 1

python -m approval_gate.cli list
python -m approval_gate.cli revoke --id pd_...
```

Standing approvals exist **only** for `charge.checkout` (buyer-initiated). Every send
(including DocuSign contracts), every call, invoice and transfer needs a per-action ID with a use limit.

### Vercel / serverless (the Next.js route)

There is no persistent disk, so set `APPROVALS_JSONL` to the signed record lines
(`--print-record` output, plus any revoke lines) and `APPROVAL_VERIFY_KEY` from your vault.
Records are signed, not secret. To revoke, remove the line or add the signed revoke line and redeploy.
With no `APPROVAL_EVENTS_PATH`, audit events go to stdout (Vercel logs) as one JSON line each.

## What the gate refuses (fail closed)

Missing / unknown / revoked ID, bad signature, wrong action, expired, used up, recipient
not in scope, amount over cap or missing, wrong currency or plan, store unreadable,
verify key missing, audit log unwritable. No env var turns it off.
