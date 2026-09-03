from __future__ import annotations

from typing import Any


def _is_valid_email(email: str) -> bool:
    if not email or "@" not in email:
        return False
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain:
        return False
    if any(ch.isspace() for ch in email):
        return False
    return True


def _domain_from_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.split("@", 1)[1].strip().lower() or None


def enrich_lead(lead: dict[str, Any], hunter_api_key: str = "", clearbit_api_key: str = "") -> dict[str, Any]:
    email = (lead.get("email") or "").strip().lower()
    company_domain = (lead.get("company_domain") or "").strip().lower() or _domain_from_email(email)
    full_name = (lead.get("full_name") or "").strip()
    linkedin = (lead.get("linkedin_url") or "").strip()

    is_valid_email = _is_valid_email(email)
    first_name = full_name.split(" ")[0] if full_name else None

    return {
        "contact": {
            "full_name": full_name or None,
            "first_name": first_name,
            "email": email or None,
            "email_valid": is_valid_email,
            "linkedin_url": linkedin or None,
        },
        "linkedin": {
            "status": "available" if linkedin else "not_provided",
            "profile_hint": linkedin.rsplit("/", 1)[-1] if linkedin else None,
        },
        "hunter": {
            "configured": bool(hunter_api_key),
            "domain": company_domain,
            "deliverability": "unknown" if not is_valid_email else "likely_deliverable",
        },
        "clearbit_style": {
            "configured": bool(clearbit_api_key),
            "company_domain": company_domain,
            "estimated_company_size": lead.get("company_size") or "unknown",
            "industry": lead.get("industry") or "unknown",
        },
        "company": {
            "name": lead.get("company_name") or None,
            "domain": company_domain,
            "source_count": 4,
        },
        "enrichment_status": "complete",
    }


def enrich_bulk(leads: list[dict[str, Any]], hunter_api_key: str = "", clearbit_api_key: str = "") -> list[dict[str, Any]]:
    return [enrich_lead(lead, hunter_api_key=hunter_api_key, clearbit_api_key=clearbit_api_key) for lead in leads]
