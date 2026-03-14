# Apex Revenue System

**Autonomous AI Revenue Platform** — Garrett Carrol

[![GENESIS Deploy](https://github.com/Garrettc123/apex-revenue-system/actions/workflows/deploy.yml/badge.svg)](https://github.com/Garrettc123/apex-revenue-system/actions/workflows/deploy.yml)
[![Scheduled Health Check](https://github.com/Garrettc123/apex-revenue-system/actions/workflows/health-check.yml/badge.svg)](https://github.com/Garrettc123/apex-revenue-system/actions/workflows/health-check.yml)

---

## Overview

GENESIS is a fully autonomous AI revenue system that:
- Generates and scores B2B leads with Google Gemini AI
- Processes crypto payments via Coinbase Commerce
- Tracks MRR, active customers, and revenue gap in real-time
- Self-heals through a topological AI task executor
- Auto-deploys to Railway on every push to `main`

---

## Quickstart

### 1. Clone & configure
```bash
git clone https://github.com/Garrettc123/apex-revenue-system.git
cd apex-revenue-system
cp .env.example .env
# Edit .env and fill in your API keys
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run locally
```bash
python main.py
# or with gunicorn:
gunicorn main:app --bind 0.0.0.0:5000
```

### 4. Run tests
```bash
pytest tests/ -v
```

### 5. Docker
```bash
docker build -t apex-revenue-system .
docker run -p 5000:5000 --env-file .env apex-revenue-system
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Landing page with pricing |
| `GET` | `/health` | System health check (JSON) |
| `GET` | `/metrics` | Revenue metrics (MRR, customers, gap) |
| `GET` | `/checkout/<plan>` | Redirect to Coinbase Commerce checkout (`starter` / `pro` / `enterprise`) |
| `GET` | `/success` | Post-payment confirmation page |
| `GET/POST` | `/genesis` | AI revenue strategy generation |
| `GET` | `/ai/leads` | AI-generated B2B lead profiles |
| `POST` | `/ai/analyze` | AI business analysis (send `{"prompt": "..."}`) |
| `POST` | `/webhook/coinbase` | Coinbase Commerce webhook handler |

---

## Deployment to Railway

### Auto-Deploy via GitHub Actions

Every push to `main` triggers the [GENESIS Deploy](.github/workflows/deploy.yml) workflow which:
1. Installs dependencies
2. Runs the full test suite (`pytest tests/ -v`)
3. Deploys to Railway (if `RAILWAY_TOKEN` secret is configured)

### Manual Setup

1. Create a project on [Railway](https://railway.app)
2. Link your GitHub repository
3. Add environment variables (see below)
4. Railway will auto-deploy on every push

### Required Secrets

Add these in **GitHub → Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `RAILWAY_TOKEN` | Railway account token — [generate here](https://railway.app/account/tokens) |
| `GEMINI_API_KEY` | Google Gemini API key — [get here](https://aistudio.google.com/app/apikey) |

### Required Environment Variables (Railway)

Set these in your Railway service variables:

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google Gemini AI key |
| `COINBASE_API_KEY` | Coinbase Commerce API key |
| `COINBASE_WEBHOOK_SECRET` | Coinbase webhook secret |
| `BASE_URL` | Your Railway service URL |

See [`.env.example`](.env.example) for full documentation.

---

## Pricing Plans

| Plan | Price | Description |
|------|-------|-------------|
| Starter | $49/mo | Solopreneurs, 5K API calls/mo |
| Pro | $149/mo | Growing businesses, 50K API calls/mo |
| Enterprise | $499/mo | Teams, unlimited API calls |

Payments processed via **Coinbase Commerce** (BTC, ETH, USDC, and more).

---

## Architecture

```
main.py                 Flask API server
├── /health             System health check
├── /metrics            Revenue metrics
├── /checkout/<plan>    Coinbase Commerce checkout
├── /webhook/coinbase   Payment webhook handler
└── /genesis, /ai/*     Gemini AI endpoints

core/
├── genesis_engine.py   Build-pipeline orchestrator
├── topological_executor.py  DAG executor with self-healing
└── agents.py           AI agent stubs (Gemini-backed)

.github/workflows/
├── deploy.yml          CI/CD: test + deploy on push to main
├── genesis-self-heal.yml  AI pipeline runner
└── health-check.yml    Scheduled uptime monitoring
```

---

## License

MIT © 2026 Garrett Carrol
