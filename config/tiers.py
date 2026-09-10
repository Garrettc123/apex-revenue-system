from __future__ import annotations

from typing import Any


TIERS: dict[str, dict[str, Any]] = {
    "free": {
        "price_usd": 0,
        "enrichments_per_month": 50,
        "bulk_enrichment": False,
        "bulk_max_batch": 0,
        "pipeline_access": "none",
    },
    "starter": {
        "price_usd": 49,
        "enrichments_per_month": 1000,
        "bulk_enrichment": True,
        "bulk_max_batch": 100,
        "pipeline_access": "basic",
    },
    "pro": {
        "price_usd": 199,
        "enrichments_per_month": 10000,
        "bulk_enrichment": True,
        "bulk_max_batch": 1000,
        "pipeline_access": "full",
    },
    "enterprise": {
        "price_usd": 999,
        "enrichments_per_month": None,
        "bulk_enrichment": True,
        "bulk_max_batch": 5000,
        "pipeline_access": "full+api",
    },
}


def normalize_tier(tier: str | None) -> str:
    if not tier:
        return "free"
    value = str(tier).strip().lower()
    return value if value in TIERS else "free"


def tier_config(tier: str | None) -> dict[str, Any]:
    return TIERS[normalize_tier(tier)]


def allows_pipeline(tier: str | None) -> bool:
    return tier_config(tier)["pipeline_access"] != "none"
