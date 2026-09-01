# Revenue Ecosystem Registry

This registry links known revenue repositories to the APEX unified revenue spine.

## Unified Spine

| Repository | Role | Spine Integration |
|---|---|---|
| [Garrettc123/apex-revenue-system](https://github.com/Garrettc123/apex-revenue-system) | Canonical ledger + metrics | Receives Coinbase, Stripe, and edge-node revenue webhooks and exposes `/metrics` |

## Webhook authentication (required)

All revenue-ingestion routes **fail closed** when the required secret is unset.

| Endpoint | Secret env var | Verification |
|---|---|---|
| `POST /webhook/coinbase` | `COINBASE_WEBHOOK_SECRET` | `X-CC-Webhook-Signature` HMAC-SHA256 of raw body |
| `POST /webhook/edge/revenue` | `EDGE_WEBHOOK_SECRET` | `X-Edge-Signature` HMAC-SHA256 hex of raw body |
| `POST /webhook/stripe` | `STRIPE_WEBHOOK_SECRET` | Stripe-Signature via `stripe.Webhook.construct_event` |

Ledger path defaults to `data/revenue_ledger.json` (override with `REVENUE_LEDGER_FILE` for durable shared storage). Events are idempotent on `charge_id`.

## Connected Revenue Repositories

| Repository | Role | Expected Revenue Flow Into Spine |
|---|---|---|
| [Garrettc123/garcar-autonomous-revenue-engine](https://github.com/Garrettc123/garcar-autonomous-revenue-engine) | Stripe-driven acquisition and checkout | Stripe webhook events forwarded to `/webhook/stripe` |
| [Garrettc123/revenue-intelligence-engine](https://github.com/Garrettc123/revenue-intelligence-engine) | Revenue intelligence and monitoring | Consumes `/metrics` for MRR state and orchestration |
| [Garrettc123/autonomous-revenue-ops](https://github.com/Garrettc123/autonomous-revenue-ops) | Revenue operations automation | Reads APEX `/metrics`; can emit normalized edge events to `/webhook/edge/revenue` |
| [Garrettc123/nwu-data-monetization](https://github.com/Garrettc123/nwu-data-monetization) | Data monetization revenue stream | Reports monetization events through normalized edge revenue payloads |
| [Garrettc123/garcar-rhns](https://github.com/Garrettc123/garcar-rhns) | Revenue horizon navigation | Consumes unified ledger-derived `/metrics` for planning and recommendations |
