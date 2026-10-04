"""Hardening audit (Oct 2026): /metrics, /integrations/status, /genesis and /ai/* need
APEX_API_KEY. Every test gets a dummy key and a Flask test client that sends it by
default, so existing endpoint tests keep exercising the handlers. The auth tests in
test_api_key_auth.py switch back to a plain client to check the 503/401/429 paths."""
import pytest
from flask.testing import FlaskClient

import api_auth

TEST_API_KEY = "unit-test-apex-key-not-real"


class KeyedClient(FlaskClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.environ_base["HTTP_X_API_KEY"] = TEST_API_KEY


@pytest.fixture(autouse=True)
def _apex_api_key(monkeypatch):
    import main
    monkeypatch.setenv("APEX_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("APEX_RATE_LIMIT_PER_MIN", "1000")
    monkeypatch.setattr(main.app, "test_client_class", KeyedClient)
    api_auth.reset_rate_limits()
    yield
    api_auth.reset_rate_limits()
