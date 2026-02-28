# Apex Revenue System

Autonomous AI Revenue Platform — Garrett Carrol

## Endpoints
- `GET /` — system status
- `GET /health` — health check
- `GET /metrics` — revenue metrics

## Deploy
Push to `main` → GitHub Actions auto-deploys to Railway.

## Secrets Required
Add these in GitHub → Settings → Secrets → Actions:
- `RAILWAY_TOKEN` — from railway.app/account/tokens
