"""Inject HashiCorp Vault secrets into process env at boot (Cloudflare Containers).

Source of truth: existing Garcar Vault layout (systems-master-hub + apex vault-secrets.yml).
Never logs secret values.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("apex.vault_inject")

STRIPE_KEYS = (
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_ID_AUDIT",
    "STRIPE_PRICE_ID_SPRINT",
    "STRIPE_PRICE_ID_RETAINER",
)
ENRICHMENT_KEYS = ("HUNTER_API_KEY", "CLEARBIT_API_KEY")

GROUP_PATHS = {
    "garcar/stripe": STRIPE_KEYS,
    "garcar/enrichment": ENRICHMENT_KEYS,
}


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _set_if_empty(key: str, value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if os.environ.get(key) and not _truthy("VAULT_FORCE"):
        return False
    os.environ[key] = text
    return True


def _read_kv_map(client: Any, path: str, mount: str) -> dict[str, Any]:
    try:
        resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point=mount)
        data = resp.get("data", {}).get("data") or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("vault read failed for %s: %s", path, type(exc).__name__)
        return {}


def inject_from_vault() -> dict[str, Any]:
    """Load stripe/enrichment secrets from Vault into os.environ. Returns status (no values)."""
    addr = (os.environ.get("VAULT_ADDR") or "").rstrip("/")
    if not addr:
        return {"injected": False, "reason": "VAULT_ADDR unset — skipping Vault inject"}

    try:
        import hvac
    except ImportError:
        return {"injected": False, "reason": "hvac not installed"}

    token = os.environ.get("VAULT_TOKEN")
    role_id = os.environ.get("VAULT_ROLE_ID")
    secret_id = os.environ.get("VAULT_SECRET_ID")
    mount = os.environ.get("VAULT_MOUNT", "secret")

    client = hvac.Client(url=addr)
    try:
        if token:
            client.token = token
        elif role_id and secret_id:
            resp = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
            client.token = resp["auth"]["client_token"]
        else:
            return {
                "injected": False,
                "reason": "Need VAULT_TOKEN or VAULT_ROLE_ID+VAULT_SECRET_ID",
            }
        if not client.is_authenticated():
            return {"injected": False, "reason": "Vault auth failed"}
    except Exception as exc:
        return {"injected": False, "reason": f"Vault auth error: {type(exc).__name__}"}

    filled: list[str] = []
    missing: list[str] = []

    for path, expected_keys in GROUP_PATHS.items():
        data = _read_kv_map(client, path, mount)
        if set(data.keys()) == {"value"} and isinstance(data.get("value"), dict):
            data = data["value"]
        for key in expected_keys:
            raw = data.get(key)
            if _set_if_empty(key, raw):
                filled.append(key)

    for key in STRIPE_KEYS + ENRICHMENT_KEYS:
        if os.environ.get(key) and not _truthy("VAULT_FORCE"):
            continue
        data = _read_kv_map(client, f"garcar/{key}", mount)
        raw = data.get("value")
        if raw is None and len(data) == 1:
            raw = next(iter(data.values()))
        if raw is None:
            raw = data.get(key)
        if _set_if_empty(key, raw):
            filled.append(key)

    for key in STRIPE_KEYS + ENRICHMENT_KEYS:
        if not os.environ.get(key):
            missing.append(key)

    status = {
        "injected": True,
        "filled_count": len(filled),
        "filled_keys": sorted(set(filled)),
        "missing_keys": missing,
        "vault_addr_set": True,
    }
    log.info(
        "vault inject complete filled=%s missing=%s",
        status["filled_count"],
        len(missing),
    )
    return status
