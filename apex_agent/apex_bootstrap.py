# apex_bootstrap.py
# Import once from main.py to register APEX routes.
# In main.py after app = Flask(__name__):
#   from apex_agent.apex_bootstrap import register_apex
#   register_apex(app)

from flask import jsonify, request
import os, hmac, hashlib
from api_auth import require_api_key
from .apex_agent_deep import ApexAgent
from .apex_ledger_sqlite import init_db, persist_agent

_apex = ApexAgent()
_db = None

def _db_conn():
    global _db
    if _db is None:
        path = os.environ.get('APEX_LEDGER_PATH', 'apex_ledger.db')
        _db = init_db(path)
    return _db

def register_apex(app):
    @app.route('/apex/report')
    @require_api_key
    def apex_report():
        return jsonify(_apex.report())

    @app.route('/webhook/hubspot/deal', methods=['POST'])
    def hubspot_deal_webhook():
        secret = os.environ.get('HUBSPOT_WEBHOOK_SECRET', '')
        if not secret:
            return jsonify({'error': 'HUBSPOT_WEBHOOK_SECRET not configured'}), 503

        sig = request.headers.get('X-HubSpot-Signature', '')
        computed = hmac.new(secret.encode(), request.data, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, sig):
            return jsonify({'error': 'invalid signature'}), 401

        payload = request.get_json(force=True, silent=True)
        if not isinstance(payload, dict):
            return jsonify({'error': 'JSON object required'}), 400
        deal = payload.get('deal') or payload.get('properties') or payload
        if not isinstance(deal, dict) or not deal:
            return jsonify({'error': 'deal object required'}), 400
        domain = deal.get('domain') or 'hubspot_deal'
        success = str(deal.get('dealstage', '')).lower() in ('closedwon', 'appointmentscheduled')
        parent = deal.get('parent_domain')
        result = _apex.encounter(domain, deal, success, parent)
        try:
            persist_agent(_apex, _db_conn())
        except Exception as e:
            result['persist_error'] = str(e)
        return jsonify(result)
