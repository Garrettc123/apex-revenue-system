# apex_hubspot_wire.py
# Wires GARCAR APEX to live HubSpot deals as encounters

import json
from typing import Any, Dict, List
try:
    from .apex_agent_deep import ApexAgent
except ImportError:  # running as a script
    from apex_agent_deep import ApexAgent

LIVE_DEALS = [
    {'id': '350807378648', 'dealname': 'Comfort Experts — LLA-47 Lead Leak Map', 'amount': 47, 'dealstage': 'appointmentscheduled', 'domain': 'trades_lead_leak_audit'},
    {'id': '350807390914', 'dealname': 'Service Champs — LLA-47 Lead Leak Map', 'amount': 47, 'dealstage': 'appointmentscheduled', 'domain': 'trades_lead_leak_audit'},
    {'id': '350454696651', 'dealname': 'Argos ARBITRAGE — SaaS Churn Rescue Batch', 'amount': 299, 'dealstage': 'appointmentscheduled', 'domain': 'saas_churn_rescue'},
    {'id': '350434898673', 'dealname': 'Argos OUTREACH — Plumbing Lead Pack DFW', 'amount': 47, 'dealstage': 'appointmentscheduled', 'domain': 'trades_lead_leak_audit'},
    {'id': '350481309387', 'dealname': 'Argos CLUSTER — Multifamily Note Stress Capture', 'amount': 299, 'dealstage': 'appointmentscheduled', 'domain': 'multifamily_note_stress'},
]

def deal_to_payload(deal):
    return {'hubspot_id': deal['id'], 'dealname': deal['dealname'], 'amount': deal['amount'], 'stage': deal['dealstage'], 'source': 'hubspot_live'}

def run_live_wire():
    agent = ApexAgent()
    results = []
    for deal in LIVE_DEALS:
        success = deal['dealstage'] in ('appointmentscheduled', 'closedwon')
        parent = 'dfw_real_estate_lead_backup' if 'Lead' in deal['dealname'] else None
        result = agent.encounter(deal['domain'], deal_to_payload(deal), success, parent)
        results.append({'deal_id': deal['id'], 'dealname': deal['dealname'], 'apex_result': result})
    return {'wired_deals': len(LIVE_DEALS), 'encounters': results, 'final_report': agent.report()}

if __name__ == '__main__':
    print(json.dumps(run_live_wire(), indent=2))
