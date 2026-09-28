"""Garcar kernel — apex-revenue-system."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

SYSTEM_ID = os.getenv("GARCAR_SYSTEM_ID", "apex-revenue-system")
SKUS = [
    {"id": "lead-leak-audit", "name": "Contractor Lead Leak Audit", "price_usd": 47, "checkout": "https://buy.stripe.com/dRm8wPbb72pY2Mz8BR43S1D", "sla_hours": 48},
    {"id": "revenue-recovery-sprint", "name": "Revenue Recovery Sprint", "price_usd": 497, "checkout": "https://buy.stripe.com/aFa8wPdjfggO3QDcS743S1F", "sla_hours": 168},
    {"id": "ai-growth-engine", "name": "AI Growth Engine", "price_usd": 1497, "checkout": "https://buy.stripe.com/9B63cv4MJ9Sq1Iv19p43S1E", "sla_hours": 720},
]
app = FastAPI(title=f"Garcar · {SYSTEM_ID}")

class FulfillmentIn(BaseModel):
    sku_id: str
    buyer_email: str
    shop_name: str | None = None

@app.get("/health")
def health():
    return {"ok": True, "system": SYSTEM_ID, "ts": datetime.now(timezone.utc).isoformat()}

@app.get("/sku")
def sku():
    return {"system": SYSTEM_ID, "skus": SKUS}

@app.get("/offer")
def offer():
    return RedirectResponse(SKUS[0]["checkout"], status_code=302)

@app.post("/fulfill")
def fulfill(body: FulfillmentIn):
    sku = next((s for s in SKUS if s["id"] == body.sku_id), None)
    if not sku:
        return {"ok": False, "error": "unknown_sku"}
    return {"ok": True, "status": "queued", "system": SYSTEM_ID, "sku": sku, "buyer_email": body.buyer_email}
