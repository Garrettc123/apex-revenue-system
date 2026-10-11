"""Tests for apex_agent package: -m entrypoints, HubSpot mapping, fallback, GET-only."""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _clear_hubspot_token(monkeypatch):
    monkeypatch.delenv('HUBSPOT_ACCESS_TOKEN', raising=False)
    monkeypatch.delenv('APEX_HUBSPOT_LIMIT', raising=False)


def test_module_run_hubspot_wire_sample(capsys, monkeypatch):
    """python -m apex_agent.apex_hubspot_wire works with sample data."""
    monkeypatch.chdir(REPO_ROOT)
    # Ensure clean import of package
    for key in list(sys.modules):
        if key == 'apex_agent' or key.startswith('apex_agent.'):
            del sys.modules[key]

    with patch('sys.argv', ['apex_hubspot_wire']):
        runpy.run_module('apex_agent.apex_hubspot_wire', run_name='__main__')

    out = capsys.readouterr().out
    assert 'Note: HUBSPOT_ACCESS_TOKEN not set' in out
    start = out.find('{')
    assert start >= 0, out
    data = json.loads(out[start:])
    assert data['wired_deals'] == 5
    assert len(data['encounters']) == 5
    assert 'final_report' in data


def test_module_run_ledger_sqlite(capsys, monkeypatch):
    """python -m apex_agent.apex_ledger_sqlite works."""
    monkeypatch.chdir(REPO_ROOT)
    for key in list(sys.modules):
        if key == 'apex_agent' or key.startswith('apex_agent.'):
            del sys.modules[key]

    with patch('sys.argv', ['apex_ledger_sqlite']):
        runpy.run_module('apex_agent.apex_ledger_sqlite', run_name='__main__')

    out = capsys.readouterr().out
    assert 'Ledger module ready' in out


def test_map_hubspot_deal_shape():
    from apex_agent.apex_hubspot_wire import map_hubspot_deal

    raw = {
        'id': '350807378648',
        'properties': {
            'dealname': 'Comfort Experts — LLA-47 Lead Leak Map',
            'amount': '47',
            'dealstage': 'appointmentscheduled',
            'closedate': None,
            'pipeline': 'default',
        },
    }
    mapped = map_hubspot_deal(raw)
    assert mapped['id'] == '350807378648'
    assert mapped['dealname'] == 'Comfort Experts — LLA-47 Lead Leak Map'
    assert mapped['amount'] == 47
    assert mapped['dealstage'] == 'appointmentscheduled'
    assert mapped['domain'] == 'trades_lead_leak_audit'
    assert mapped['pipeline'] == 'default'


def test_fetch_hubspot_deals_mocked():
    from apex_agent.apex_hubspot_wire import fetch_hubspot_deals

    page = {
        'results': [
            {
                'id': '1',
                'properties': {
                    'dealname': 'SaaS Churn Rescue',
                    'amount': '299.0',
                    'dealstage': 'closedwon',
                    'closedate': '2026-01-01T00:00:00.000Z',
                    'pipeline': 'default',
                },
            }
        ],
        'paging': {},
    }
    get_json = MagicMock(return_value=page)
    deals = fetch_hubspot_deals(token='test-token-not-printed', limit=10, get_json=get_json)
    assert len(deals) == 1
    assert deals[0]['domain'] == 'saas_churn_rescue'
    assert deals[0]['amount'] == 299
    get_json.assert_called_once()
    url, token = get_json.call_args[0]
    assert url.startswith('https://api.hubapi.com/crm/v3/objects/deals?')
    assert 'properties=' in url
    assert token == 'test-token-not-printed'


def test_fallback_when_no_token(capsys):
    from apex_agent.apex_hubspot_wire import LIVE_DEALS, load_deals

    deals = load_deals()
    assert deals == LIVE_DEALS
    assert 'HUBSPOT_ACCESS_TOKEN not set' in capsys.readouterr().out


def test_fallback_when_fetch_fails(capsys, monkeypatch):
    from apex_agent import apex_hubspot_wire as wire

    monkeypatch.setenv('HUBSPOT_ACCESS_TOKEN', 'fake-token')

    def boom(*_a, **_k):
        raise RuntimeError('network down')

    monkeypatch.setattr(wire, 'fetch_hubspot_deals', boom)
    deals = wire.load_deals()
    assert deals == wire.LIVE_DEALS
    out = capsys.readouterr().out
    assert 'HubSpot deal fetch failed' in out
    assert 'fake-token' not in out


def test_hubspot_get_only_method():
    """_hubspot_get_json must only issue GET via urllib Request.method."""
    from apex_agent.apex_hubspot_wire import _hubspot_get_json

    captured = {}

    class FakeResp:
        def read(self):
            return b'{"results":[],"paging":{}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured['method'] = getattr(req, 'get_method', lambda: req.get_method())()
        captured['has_data'] = req.data is not None
        return FakeResp()

    with patch('urllib.request.urlopen', side_effect=fake_urlopen):
        _hubspot_get_json('https://api.hubapi.com/crm/v3/objects/deals', 'tok')

    assert captured['method'] == 'GET'
    assert captured['has_data'] is False


def test_no_non_get_in_wire_source():
    """Static guard: wire module must not call HubSpot with write verbs."""
    src = (REPO_ROOT / 'apex_agent' / 'apex_hubspot_wire.py').read_text()
    for bad in ('method="POST"', "method='POST'", 'method="PUT"', "method='PUT'",
                'method="PATCH"', "method='PATCH'", 'method="DELETE"', "method='DELETE'",
                '.post(', '.put(', '.patch(', '.delete('):
        assert bad not in src, f'forbidden write pattern in source: {bad}'
    assert "method='GET'" in src or 'method="GET"' in src
