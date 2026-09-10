# Phase 1–2 — Enrichment + Stripe (no RHNS)

## Deploy surface (locked)

**Cloudflare Containers** using the repo `Dockerfile` (`gunicorn main:app`).

- Not Workers (no native Flask/WSGI)
- Not Pages (static / edge HTML — not this API)

## Endpoints

- `POST /api/enrich` — body: `{ "customer_id", "lead": { email, full_name, ... } }`
- `POST /api/enrich/bulk` — body: `{ "customer_id", "leads": [ ... ] }` (sprint/retainer)
- `POST /api/checkout` — body: `{ "customer_id", "tier": "audit|sprint|retainer" }`
- `POST /api/stripe/webhook` — Stripe subscription lifecycle
- `GET /api/tiers` — public tier map ($47 / $497 / $1497)

## Secrets / vars (names only) — HashiCorp Vault vs Cloudflare

Do **not** invent a new vault stack. Reuse the existing apex `vault-secrets.yml` wiring and systems-master-hub patterns.

### HashiCorp Vault (app runtime secrets)

Pulled by the container via existing Vault auth (AppRole `VAULT_ROLE_ID` / `VAULT_SECRET_ID`, or JWT role) against `VAULT_ADDR` — same paths already used by apex:

| Vault path | Env names (no values) |
|---|---|
| `secret/data/garcar/stripe` | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_AUDIT`, `STRIPE_PRICE_ID_SPRINT`, `STRIPE_PRICE_ID_RETAINER` |
| `secret/data/garcar/enrichment` | `HUNTER_API_KEY`, `CLEARBIT_API_KEY` |

### Cloudflare dashboard / platform only

Set in CF Containers (or Wrangler) — not duplicated as a new secret store:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN` (platform deploy: Containers + Workers edit)
- `VAULT_ADDR`
- `VAULT_ROLE_ID` / `VAULT_SECRET_ID` (AppRole) **or** JWT role credentials so the container can pull from Vault
- `BASE_URL` (public CF hostname)
- `REVENUE_DB_FILE` (path on durable volume — SQLite is ephemeral without a mount)
- `PORT` (platform usually sets this)

Never paste secret values in chat, PRs, or docs. Skip Railway/Render for this deploy path.


## Runtime injection (Cloudflare Containers)

At process boot, `core/vault_inject.py` → `inject_from_vault()`:

1. Auth with `VAULT_ADDR` + (`VAULT_ROLE_ID`/`VAULT_SECRET_ID` or `VAULT_TOKEN`)
2. Read `secret/garcar/stripe` and `secret/garcar/enrichment` (and/or per-key `secret/garcar/STRIPE_*`)
3. Fill empty process env for Stripe + enrichment keys — **real values from Vault**, never placeholders in code

CF dashboard still holds only `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` for deploy. App business secrets come from Vault.
