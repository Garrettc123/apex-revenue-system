"""
Vercel Python serverless entrypoint.
Exposes the Flask app so all routes (/health, /metrics, webhooks, etc.) work.
"""
from main import app as app
