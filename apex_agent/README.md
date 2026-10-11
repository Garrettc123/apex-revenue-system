# GARCAR APEX Agent

Sovereign expert agent integrated into apex-revenue-system.

- `apex_agent_deep.py` — core with RHNS constraints, continuous state, mastery ladder, skill transfer
- `apex_hubspot_wire.py` — live HubSpot deal encounters
- `apex_ledger_sqlite.py` — Merkle-chained ledger persistence (Supabase drop-in)

Run: `python -m apex_agent.apex_hubspot_wire`