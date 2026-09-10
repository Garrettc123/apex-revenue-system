"""Cloudflare Containers entry: load Flask app then register Phase 1–2 (Vault inject at boot)."""
from main import app
from core.phase12_api import register_phase12_routes

register_phase12_routes(app)
