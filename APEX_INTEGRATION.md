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
