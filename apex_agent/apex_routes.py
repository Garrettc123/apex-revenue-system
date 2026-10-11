# apex_routes.py — drop-in routes for apex-revenue-system/main.py
# Add these to main.py after the existing webhook routes.

from flask import jsonify, request
import os, json, hmac, hashlib
from apex_agent.apex_agent_deep import ApexAgent, mastery_label
from apex_agent.apex_ledger_sqlite import init_db, persist_agent

# Singleton agent (persists in process; restart resets unless ledger is loaded)
_apex = ApexAgent()
_apex_db = None

def _get_apex_db():
    global _apex_db
    if _apex_db is None:
        db_path = os.environ.get('APEX_LEDGER_PATH', 'apex_ledger.db')
        # If Supabase is configured, prefer it; fall back to SQLite
        if os.environ.get('SUPABASE_URL') and os.environ.get('SUPABASE_SERVICE_ROLE_KEY'):
            # Placeholder: real Supabase client would go here
            # For now keep SQLite as the durable local ledger
            pass
        _apex_db = init_db(db_path)
    return _apex_db

@app.route('/apex/report')
def apex_report():
    """Return current APEX mastery and ledger state."""
    return jsonify(_apex.report())

@app.route('/webhook/hubspot/deal', methods=['POST'])
def hubspot_deal_webhook():
    """Real HubSpot deal event → APEX encounter.
    Expects HubSpot workflow webhook or private app subscription payload.
    """
    secret = os.environ.get('HUBSPOT_WEBHOOK_SECRET', '')
    if secret:
        sig = request.headers.get('X-HubSpot-Signature', '')
        computed = hmac.new(secret.encode(), request.data, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, sig):
            return jsonify({'error': 'invalid signature'}), 401

    payload = request.get_json(force=True, silent=True) or {}
    # Normalize common HubSpot shapes
    deal = payload.get('deal') or payload.get('properties') or payload
    domain = deal.get('domain') or deal.get('hs_deal_domain') or 'hubspot_deal'
    success = str(deal.get('dealstage', '')).lower() in ('closedwon', 'appointmentscheduled')
    parent = deal.get('parent_domain')

    result = _apex.encounter(
        domain=domain,
        payload=deal,
        success=success,
        parent_domain=parent,
    )
    # Persist after every encounter
    try:
        persist_agent(_apex, _get_apex_db())
    except Exception as e:
        result['persist_error'] = str(e)
    return jsonify(result)
