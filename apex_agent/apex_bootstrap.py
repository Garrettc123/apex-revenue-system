# apex_bootstrap.py
# Import once from main.py to register APEX routes.
# In main.py after app = Flask(__name__):
#   from apex_agent.apex_bootstrap import register_apex
#   register_apex(app)

from flask import jsonify, request
import base64
import os, hmac, hashlib, time
from api_auth import require_api_key
from .apex_agent_deep import ApexAgent
from .apex_ledger_sqlite import init_db, persist_agent

_apex = ApexAgent()
_db = None


def _hubspot_signature_valid(secret: str) -> bool:
    """Validate HubSpot v3 signatures (preferred) and legacy v1/v2 signatures."""
    raw_body = request.get_data(cache=True, as_text=True)

    signature_v3 = request.headers.get("X-HubSpot-Signature-v3", "")
    timestamp = request.headers.get("X-HubSpot-Request-Timestamp", "")
    if signature_v3:
        try:
            timestamp_ms = int(timestamp)
        except (TypeError, ValueError):
            return False
        # Reject replayed requests and far-future timestamps.
        if abs(int(time.time() * 1000) - timestamp_ms) > 300_000:
            return False

        uri = request.url
        # HubSpot's canonical URI decodes these specific percent-encoded characters.
        for encoded, decoded in (
            ("%3A", ":"), ("%2F", "/"), ("%3F", "?"), ("%40", "@"),
            ("%21", "!"), ("%24", "$"), ("%27", "'"), ("%28", "("),
            ("%29", ")"), ("%2A", "*"), ("%2C", ","), ("%3B", ";"),
        ):
            uri = uri.replace(encoded, decoded).replace(encoded.lower(), decoded)

        source = request.method + uri + raw_body + timestamp
        digest = base64.b64encode(
            hmac.new(secret.encode("utf-8"), source.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")
        return hmac.compare_digest(digest, signature_v3)

    signature = request.headers.get("X-HubSpot-Signature", "")
    version = (request.headers.get("X-HubSpot-Signature-Version") or "v1").lower()
    if version == "v1":
        source = secret + raw_body
    elif version == "v2":
        source = secret + request.method + request.url + raw_body
    else:
        return False
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return bool(signature) and hmac.compare_digest(digest, signature)


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

        if not _hubspot_signature_valid(secret):
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
