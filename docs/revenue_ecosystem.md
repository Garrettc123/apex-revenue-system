# Revenue Ecosystem Registry

This registry links known revenue repositories to the APEX unified revenue spine.

## Unified Spine

| Repository | Role | Spine Integration |
|---|---|---|
| [Garrettc123/apex-revenue-system](https://github.com/Garrettc123/apex-revenue-system) | Canonical ledger + metrics | Receives Coinbase, Stripe, and edge-node revenue webhooks and exposes `/metrics` |

## Connected Revenue Repositories

| Repository | Role | Expected Revenue Flow Into Spine |
|---|---|---|
| [Garrettc123/garcar-autonomous-revenue-engine](https://github.com/Garrettc123/garcar-autonomous-revenue-engine) | Stripe-driven acquisition and checkout | Stripe webhook events forwarded to `/webhook/stripe` |
| [Garrettc123/revenue-intelligence-engine](https://github.com/Garrettc123/revenue-intelligence-engine) | Revenue intelligence and monitoring | Consumes `/metrics` for MRR state and orchestration |
| [Garrettc123/autonomous-revenue-ops](https://github.com/Garrettc123/autonomous-revenue-ops) | Revenue operations automation | Reads APEX `/metrics`; can emit normalized edge events to `/webhook/edge/revenue` |
| [Garrettc123/nwu-data-monetization](https://github.com/Garrettc123/nwu-data-monetization) | Data monetization revenue stream | Reports monetization events through normalized edge revenue payloads |
| [Garrettc123/garcar-rhns](https://github.com/Garrettc123/garcar-rhns) | Revenue horizon navigation | Consumes unified ledger-derived `/metrics` for planning and recommendations |
