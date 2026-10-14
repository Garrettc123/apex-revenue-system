# GARCAR APEX Agent

Sovereign expert agent integrated into apex-revenue-system.

- `apex_agent_deep.py` — core with RHNS constraints, continuous state, mastery ladder, skill transfer
- `apex_hubspot_wire.py` — HubSpot deal encounters (sample data, or live read-only deals)
- `apex_ledger_sqlite.py` — SQLite ledger persistence (path configurable via `APEX_LEDGER_PATH`)
- `apex_routes.py` — drop-in Flask route snippets: `GET /apex/report` and `POST /webhook/hubspot/deal`
- `apex_bootstrap.py` — preferred registration: `from apex_agent.apex_bootstrap import register_apex` then `register_apex(app)` (see `APEX_INTEGRATION.md`)

## Run

From the repo root:

```bash
python -m apex_agent.apex_hubspot_wire
python -m apex_agent.apex_ledger_sqlite
```

Without a HubSpot token, the wire uses built-in sample deals.

## Live HubSpot deals (read-only)

Set a private app access token so the wire can **GET** deals from HubSpot:

```bash
export HUBSPOT_ACCESS_TOKEN=pat-...
# optional: max deals to load (default 100)
export APEX_HUBSPOT_LIMIT=100
python -m apex_agent.apex_hubspot_wire
```

**Scope required:** `crm.objects.deals.read` only.

**Read-only:** `apex_hubspot_wire.py` only issues GET requests to HubSpot. It never creates, updates, or deletes HubSpot records. If the token is missing or a request fails, it prints a short note and falls back to sample deals. The token is never printed.

## Ledger path

`init_db()` uses a local SQLite file. Routes can override the path with env `APEX_LEDGER_PATH` (SQLite remains the durable local ledger).
