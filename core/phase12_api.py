from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, request

from config.tiers import normalize_tier, tier_config
from core import lead_enrichment, payments


def _db_path() -> str:
    return os.environ.get("REVENUE_DB_FILE", os.path.join("data", "revenue.db"))


def _price_ids() -> dict[str, str]:
    return {
        "audit": os.environ.get("STRIPE_PRICE_ID_AUDIT", ""),
        "sprint": os.environ.get("STRIPE_PRICE_ID_SPRINT", ""),
        "retainer": os.environ.get("STRIPE_PRICE_ID_RETAINER", ""),
    }


def register_phase12_routes(app: Flask) -> None:
    """Phase 1-2 only: enrichment + Stripe tiers. No RHNS / deal pipeline."""
    payments.init_db(_db_path())

    @app.route("/api/enrich", methods=["POST"])
    def api_enrich():
        payload = request.get_json(force=True, silent=True) or {}
        customer_id = str(payload.get("customer_id") or "").strip()
        if not customer_id:
            return jsonify({"error": "customer_id is required"}), 400
        lead = payload.get("lead") if isinstance(payload.get("lead"), dict) else payload
        ok, reason, customer = payments.check_enrichment_access(_db_path(), customer_id)
        if not ok:
            return jsonify({"error": reason, "customer": customer}), 402
        payments.provision_customer(_db_path(), customer_id=customer_id, tier=customer.get("tier", "free"))
        result = lead_enrichment.enrich_lead(
            lead,
            hunter_api_key=os.environ.get("HUNTER_API_KEY", ""),
            clearbit_api_key=os.environ.get("CLEARBIT_API_KEY", ""),
        )
        rid = payments.record_enrichment(_db_path(), customer_id, lead, result)
        return jsonify({"status": "ok", "enrichment_id": rid, "tier": customer.get("tier"), "result": result})

    @app.route("/api/enrich/bulk", methods=["POST"])
    def api_enrich_bulk():
        payload = request.get_json(force=True, silent=True) or {}
        customer_id = str(payload.get("customer_id") or "").strip()
        leads = payload.get("leads")
        if not customer_id:
            return jsonify({"error": "customer_id is required"}), 400
        if not isinstance(leads, list) or not leads:
            return jsonify({"error": "leads must be a non-empty array"}), 400
        ok, reason, customer = payments.check_enrichment_access(_db_path(), customer_id)
        if not ok:
            return jsonify({"error": reason, "customer": customer}), 402
        cfg = tier_config(customer.get("tier"))
        if not cfg.get("bulk_enrichment"):
            return jsonify({"error": "Bulk enrichment requires sprint or retainer tier", "tier": customer.get("tier")}), 402
        max_batch = int(cfg.get("bulk_max_batch") or 0)
        if max_batch and len(leads) > max_batch:
            return jsonify({"error": f"Batch exceeds tier max of {max_batch}"}), 400
        results = lead_enrichment.enrich_bulk(
            leads,
            hunter_api_key=os.environ.get("HUNTER_API_KEY", ""),
            clearbit_api_key=os.environ.get("CLEARBIT_API_KEY", ""),
        )
        ids = []
        for lead, result in zip(leads, results):
            if not isinstance(lead, dict):
                continue
            ids.append(payments.record_enrichment(_db_path(), customer_id, lead, result))
        return jsonify({"status": "ok", "count": len(ids), "enrichment_ids": ids, "results": results})

    @app.route("/api/checkout", methods=["POST"])
    def api_checkout():
        payload = request.get_json(force=True, silent=True) or {}
        customer_id = str(payload.get("customer_id") or "").strip()
        tier = normalize_tier(payload.get("tier"))
        if not customer_id:
            return jsonify({"error": "customer_id is required"}), 400
        if tier == "free":
            return jsonify({"error": "Free tier does not require checkout"}), 400
        base_url = os.environ.get("BASE_URL", "https://your-apex-container.example.com")
        try:
            session = payments.create_checkout_session(
                customer_id=customer_id,
                tier=tier,
                base_url=base_url,
                price_ids=_price_ids(),
                stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        payments.provision_customer(_db_path(), customer_id=customer_id, email=payload.get("email"), tier="free")
        return jsonify({"status": "created", "checkout": session})

    @app.route("/api/stripe/webhook", methods=["POST"])
    def api_stripe_webhook():
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        try:
            event = payments.verify_webhook_signature(request.data, request.headers.get("Stripe-Signature", ""), secret)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        payments.handle_webhook_event(_db_path(), event if isinstance(event, dict) else {})
        return jsonify({"status": "ok"})

    @app.route("/api/tiers", methods=["GET"])
    def api_tiers():
        from config.tiers import TIERS
        return jsonify({"tiers": TIERS})
