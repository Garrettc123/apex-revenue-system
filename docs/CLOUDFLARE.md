# Cloudflare deploy — apex-revenue-system

**Decision:** Cloudflare Containers (Dockerfile).

App secrets come from **HashiCorp Vault** (existing Garcar paths). See `docs/PHASE12.md`.

CF dashboard supplies only deploy identity: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`.

After merge: build/push container from `Dockerfile`, map Vault → container env, health check `GET /health`.
