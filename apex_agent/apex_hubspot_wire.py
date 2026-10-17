# apex_hubspot_wire.py
# Wires GARCAR APEX to live HubSpot deals as encounters
# Read-only HubSpot access: GET only; never writes to HubSpot.

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from .apex_agent_deep import ApexAgent
except ImportError:  # running as a script (python apex_agent/apex_hubspot_wire.py)
    from apex_agent_deep import ApexAgent

HUBSPOT_DEALS_URL = 'https://api.hubapi.com/crm/v3/objects/deals'
HUBSPOT_DEAL_PROPERTIES = ('dealname', 'amount', 'dealstage', 'closedate', 'pipeline')
DEFAULT_HUBSPOT_LIMIT = 100

LIVE_DEALS = [
    {'id': '350807378648', 'dealname': 'Comfort Experts — LLA-47 Lead Leak Map', 'amount': 47, 'dealstage': 'appointmentscheduled', 'domain': 'trades_lead_leak_audit'},
    {'id': '350807390914', 'dealname': 'Service Champs — LLA-47 Lead Leak Map', 'amount': 47, 'dealstage': 'appointmentscheduled', 'domain': 'trades_lead_leak_audit'},
    {'id': '350454696651', 'dealname': 'Argos ARBITRAGE — SaaS Churn Rescue Batch', 'amount': 299, 'dealstage': 'appointmentscheduled', 'domain': 'saas_churn_rescue'},
    {'id': '350434898673', 'dealname': 'Argos OUTREACH — Plumbing Lead Pack DFW', 'amount': 47, 'dealstage': 'appointmentscheduled', 'domain': 'trades_lead_leak_audit'},
    {'id': '350481309387', 'dealname': 'Argos CLUSTER — Multifamily Note Stress Capture', 'amount': 299, 'dealstage': 'appointmentscheduled', 'domain': 'multifamily_note_stress'},
]


def deal_to_payload(deal):
    return {'hubspot_id': deal['id'], 'dealname': deal['dealname'], 'amount': deal['amount'], 'stage': deal['dealstage'], 'source': 'hubspot_live'}


def infer_domain(dealname: str) -> str:
    """Map deal name keywords into the sample-deal domain labels."""
    name = (dealname or '').lower()
    if 'churn' in name or 'saas' in name:
        return 'saas_churn_rescue'
    if 'multifamily' in name or 'note stress' in name:
        return 'multifamily_note_stress'
    if 'lead' in name or 'lla' in name or 'plumbing' in name:
        return 'trades_lead_leak_audit'
    return 'hubspot_deal'


def _parse_amount(raw: Any) -> Any:
    if raw is None or raw == '':
        return 0
    try:
        num = float(raw)
        return int(num) if num.is_integer() else num
    except (TypeError, ValueError):
        return raw


def map_hubspot_deal(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map a HubSpot CRM v3 deal object into the wire's flat deal shape."""
    props = raw.get('properties') or {}
    dealname = props.get('dealname') or ''
    return {
        'id': str(raw.get('id', '')),
        'dealname': dealname,
        'amount': _parse_amount(props.get('amount')),
        'dealstage': props.get('dealstage') or '',
        'domain': infer_domain(dealname),
        'closedate': props.get('closedate'),
        'pipeline': props.get('pipeline'),
    }


def _hubspot_get_json(url: str, token: str) -> Dict[str, Any]:
    """Perform a single GET against HubSpot. Never uses non-GET methods."""
    req = urllib.request.Request(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
        },
        method='GET',
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_hubspot_deals(
    token: Optional[str] = None,
    limit: Optional[int] = None,
    get_json=_hubspot_get_json,
) -> List[Dict[str, Any]]:
    """
    Read-only: paginate GET /crm/v3/objects/deals and map into wire shape.
    Cap total deals via APEX_HUBSPOT_LIMIT (default 100).
    """
    token = token if token is not None else os.environ.get('HUBSPOT_ACCESS_TOKEN')
    if not token:
        raise ValueError('HUBSPOT_ACCESS_TOKEN is not set')

    if limit is None:
        try:
            limit = int(os.environ.get('APEX_HUBSPOT_LIMIT', DEFAULT_HUBSPOT_LIMIT))
        except ValueError:
            limit = DEFAULT_HUBSPOT_LIMIT
    limit = max(1, limit)

    deals: List[Dict[str, Any]] = []
    after: Optional[str] = None
    page_size = min(100, limit)

    while len(deals) < limit:
        params = {
            'properties': ','.join(HUBSPOT_DEAL_PROPERTIES),
            'limit': min(page_size, limit - len(deals)),
        }
        if after:
            params['after'] = after
        url = f'{HUBSPOT_DEALS_URL}?{urllib.parse.urlencode(params)}'
        data = get_json(url, token)
        for raw in data.get('results') or []:
            deals.append(map_hubspot_deal(raw))
            if len(deals) >= limit:
                break
        paging = (data.get('paging') or {}).get('next') or {}
        next_after = paging.get('after')
        if not next_after:
            break
        if next_after == after:
            raise ValueError('HubSpot pagination cursor did not advance')
        after = next_after

    return deals


def load_deals() -> List[Dict[str, Any]]:
    """
    Prefer live HubSpot deals when HUBSPOT_ACCESS_TOKEN is set.
    On missing token or any fetch failure, print a clear note and use samples.
    Never prints the token.
    """
    token = os.environ.get('HUBSPOT_ACCESS_TOKEN')
    if not token:
        print('Note: HUBSPOT_ACCESS_TOKEN not set; using sample deals.')
        return list(LIVE_DEALS)

    try:
        deals = fetch_hubspot_deals(token=token)
        print(f'Loaded {len(deals)} deal(s) from HubSpot (read-only GET).')
        return deals
    except Exception as exc:  # network, auth, parse — fall back
        print('Note: HubSpot deal fetch failed; using sample deals.')
        return list(LIVE_DEALS)


def run_live_wire(deals: Optional[List[Dict[str, Any]]] = None):
    if deals is None:
        deals = load_deals()
    agent = ApexAgent()
    results = []
    for deal in deals:
        success = deal['dealstage'] in ('appointmentscheduled', 'closedwon')
        parent = 'dfw_real_estate_lead_backup' if 'Lead' in (deal.get('dealname') or '') else None
        result = agent.encounter(deal['domain'], deal_to_payload(deal), success, parent)
        results.append({'deal_id': deal['id'], 'dealname': deal['dealname'], 'apex_result': result})
    return {'wired_deals': len(deals), 'encounters': results, 'final_report': agent.report()}


if __name__ == '__main__':
    print(json.dumps(run_live_wire(), indent=2))
