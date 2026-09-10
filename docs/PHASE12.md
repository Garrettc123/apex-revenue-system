# Phase 1–2 — Enrichment + Stripe (no RHNS)

## Deploy surface (locked)

**Cloudflare Containers** using the repo `Dockerfile` (`gunicorn main:app`).

- Not Workers (no native Flask/WSGI)
- Not Pages (static / edge HTML — not this API)
- Not Railway / Render as primary

## Secrets — HashiCorp Vault (source of truth)

Use the **existing** Garcar HashiCorp stack (`systems-master-hub` + apex `.github/workflows/vault-secrets.yml`). Do not invent a new secrets product.

Canonical layout: `secret/garcar/<KEY>` (KV v2). Apex workflow also maps platform groups:

| Vault path / key | Env names used by Phase 1–2 |
|---|---|
| `secret/data/garcar/stripe` (or `secret/garcar/STRIPE_*`) | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_AUDIT`, `STRIPE_PRICE_ID_SPRINT`, `STRIPE_PRICE_ID_RETAINER` |
| `secret/data/garcar/enrichment` (or `secret/garcar/HUNTER_*` / `CLEARBIT_*`) | `HUNTER_API_KEY`, `CLEARBIT_API_KEY` |

Bootstrap / sync (already documented in systems-master-hub `docs/HASHICORP-VAULT.md`):

- `VAULT_ADDR`
- `VAULT_ROLE_ID` + `VAULT_SECRET_ID` (preferred AppRole)
- JWT role `garcar-github-actions` for Actions import

Runtime on Cloudflare Containers: inject the same env names into the container (Vault Agent / sync → CF secrets). Never paste values in chat.

## Cloudflare dashboard only (platform)

These are **not** app business secrets — set in CF / Wrangler for deploy:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

Also set on the container (non-secret config):

- `BASE_URL` — public Containers hostname
- `REVENUE_DB_FILE` — path on a durable volume (SQLite is ephemeral without a mount)
- `PORT` — usually set by the platform

## Endpoints

- `POST /api/enrich`
- `POST /api/enrich/bulk` (sprint/retainer)
- `POST /api/checkout` — tier `audit|sprint|retainer` ($47 / $497 / $1497)
- `POST /api/stripe/webhook`
- `GET /api/tiers`
