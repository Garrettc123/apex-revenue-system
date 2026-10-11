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
