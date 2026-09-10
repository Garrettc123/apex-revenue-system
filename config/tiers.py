from __future__ import annotations

from typing import Any


TIERS: dict[str, dict[str, Any]] = {
    "free": {
        "price_usd": 0,
        "enrichments_per_month": 25,
        "bulk_enrichment": False,
        "bulk_max_batch": 0,
    },
    "audit": {
        "price_usd": 47,
        "enrichments_per_month": 200,
        "bulk_enrichment": False,
        "bulk_max_batch": 0,
    },
    "sprint": {
        "price_usd": 497,
        "enrichments_per_month": 2000,
        "bulk_enrichment": True,
        "bulk_max_batch": 100,
    },
    "retainer": {
        "price_usd": 1497,
        "enrichments_per_month": None,
        "bulk_enrichment": True,
        "bulk_max_batch": 1000,
    },
}

_LEGACY = {
    "starter": "audit",
    "pro": "sprint",
    "enterprise": "retainer",
}


def normalize_tier(tier: str | None) -> str:
    if not tier:
        return "free"
    value = str(tier).strip().lower()
    value = _LEGACY.get(value, value)
    return value if value in TIERS else "free"


def tier_config(tier: str | None) -> dict[str, Any]:
    return TIERS[normalize_tier(tier)]
