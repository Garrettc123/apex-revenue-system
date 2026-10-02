"""Shared fixtures for the GAR-530 approval-gate tests (stdlib + pytest only).

Kept out of conftest.py because the repo-root conftest.py already owns that module name.
Test modules import the fixtures from here.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Dummy key used only by the test suite. Not a real secret.
TEST_KEY = "test-only-key-" + "x" * 40


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    store = tmp_path / "approvals" / "approvals.jsonl"
    store.parent.mkdir()
    store.touch()
    monkeypatch.setenv("APPROVALS_PATH", str(store))
    for var in ("APPROVALS_USED_PATH", "EVENTS_PATH", "APPROVALS_JSONL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APPROVAL_EVENTS_PATH", str(tmp_path / "approvals" / "policy_events.jsonl"))
    monkeypatch.setenv("APPROVAL_VERIFY_KEY", TEST_KEY)
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", TEST_KEY)
    return tmp_path / "approvals"


@pytest.fixture
def grant(gate_env, capsys):
    """grant('--action', 'send.contract', '--to', 'a@x.com') -> policy_decision_id"""
    from approval_gate import cli

    def _grant(*args):
        capsys.readouterr()
        assert cli.main(["grant", *args]) == 0
        return capsys.readouterr().out.strip().splitlines()[0]

    return _grant


def read_events(gate_env):
    p = gate_env / "policy_events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
