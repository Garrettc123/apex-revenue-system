# APEX Integration

Add these two lines to `main.py` after `app = Flask(__name__)`:

```python
from apex_agent.apex_bootstrap import register_apex
register_apex(app)
```

This registers:
- `GET /apex/report` — current mastery and ledger state
- `POST /webhook/hubspot/deal` — live HubSpot deal webhook → APEX encounter + ledger persist

Ledger uses `APEX_LEDGER_PATH` (defaults to `apex_ledger.db`). Supabase env vars are already checked in the revenue system.


## Security requirements (October 2026)

- Set a strong, private `APEX_API_KEY` in the runtime secret store. `GET /apex/report`
  and Phase 1–2 mutation endpoints (`POST /api/enrich`, `POST /api/enrich/bulk`,
  and `POST /api/checkout`) now fail closed unless a valid key is sent as
  `X-API-Key` or `Authorization: Bearer <key>`.
- Set `HUBSPOT_WEBHOOK_SECRET` before enabling `POST /webhook/hubspot/deal`.
  Missing configuration returns HTTP 503; invalid signatures return HTTP 401. HubSpot v3
  signatures are timestamp-checked (5-minute maximum clock skew/replay window); legacy
  v1/v2 signatures are also supported. If the reverse proxy changes the URL seen by Flask,
  set `HUBSPOT_WEBHOOK_PUBLIC_URL` to the exact public webhook URL configured in HubSpot.
- Install the `stripe` Python dependency. The checkout path no longer returns
  a synthetic/mock checkout URL when the Stripe SDK is missing.
- These code controls do not configure production secrets, prove a successful
  live payment, or demonstrate that downstream connectors are deployed.


## Revenue reporting and billing semantics

- The public `/checkout/<plan>` route uses Coinbase Commerce fixed-price charges. Those buttons are one-time purchases, not recurring subscriptions; the checkout labels therefore say “one-time.”
- `/metrics` separates `confirmed_revenue_usd`, `one_time_revenue_usd`, `subscription_invoice_revenue_usd`, and `mrr_usd`. Only signed `invoice.paid` events with an explicitly recognized recurring price interval contribute monthly-equivalent MRR.
- Standalone Stripe `payment_intent.succeeded` events are one-time revenue. Related `charge.succeeded` events and PaymentIntents attached to invoices are not separately counted, preventing double-counting.
- Historical ledger events without a `revenue_type` are conservatively treated as one-time revenue; no subscription status is inferred from a plan label.
- Customer counts are based on distinct known customer IDs, emails, or tenant IDs. Transactions without a known customer identity still count as revenue but do not inflate customer counts.
- If the revenue ledger file is corrupt or unreadable, writes fail closed instead of resetting the ledger and overwriting historical records.

The separate Stripe subscription routes use configured Stripe Price IDs and require their own deployment and end-to-end validation. Code presence alone is not proof those routes are configured or live.
