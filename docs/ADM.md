# Autonomous Deployment Mesh (ADM)

Canonical home (with `garcar-enterprise-production`). Human owns every merge unless a PR is explicitly labeled `automerge`.

## Path (no Cursor Cloud Agents)

1. Branch off `main`
2. Open PR → **ADM CI** must pass (`ci.yml`)
3. Founder reviews and merges (or applies `automerge` label intentionally)
4. Push to `main` runs **GENESIS Deploy** (Railway) when `RAILWAY_TOKEN` is set
5. Health: `GET /health`

## Secrets

- `RAILWAY_TOKEN` — Railway deploy from Actions

## Proof

Put CI run links and health output in the PR body. Do not commit media into the branch.
