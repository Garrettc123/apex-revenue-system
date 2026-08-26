# APEX Revenue Ecosystem Registry

This registry links known revenue repositories to the APEX revenue spine in this repository.

## Unified Spine

- **System of record**: `apex-revenue-system`
- **Ingestion endpoints**: `/webhook/coinbase`, `/webhook/stripe`, `/webhook/edge/revenue`
- **Unified metrics surface**: `/metrics`

## Known Revenue Repositories

| Repository | Role | Integration path to APEX spine |
|---|---|---|
| `Garrettc123/apex-revenue-system` | Unified ledger + metrics | Direct (native) |
| `Garrettc123/revenue-intelligence-engine` | Cross-stream revenue intelligence | Push revenue signals/events into `/webhook/edge/revenue` or `/webhook/stripe` |
| `Garrettc123/autonomous-revenue-ops` | Revenue operations automation | Post confirmed payment events to `/webhook/edge/revenue` |
| `Garrettc123/garcar-autonomous-revenue-engine` | Stripe-driven checkout/revenue engine | Send Stripe success webhooks to `/webhook/stripe` |
| `Garrettc123/nwu-data-monetization` | Data monetization revenue stream | Convert monetization settlements into unified revenue events |

## Event Contract Note

Use the `EdgeRevenueEvent` schema in `docs/api/openapi.yaml` for edge revenue events so all systems emit a consistent payload into the unified ledger.
