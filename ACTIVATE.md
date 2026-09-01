# ACTIVATE — apex-revenue-system (Vault-native)

Shared Garcar Vault plane (bootstrapped from `autonomous-butler-core`).

## Required

1. `VAULT_ADDR` GitHub Actions secret
2. Secrets written under `secret/data/garcar/stripe`, `ai`, `enrichment`, `github`

```bash
# One-time from butler-core
./vault/automater/automate-all.sh
```
