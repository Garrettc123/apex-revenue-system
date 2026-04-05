# Pixel 10 Edge Node — Onboarding Checklist

This document describes the steps to bring a Pixel 10 device online as an
active edge node in the Apex Revenue System.

## Prerequisites

- Google Pixel 10 running Android 16+
- Stable 5G or Wi-Fi connectivity to the Apex cloud endpoint
- The device agent APK installed (see internal build artifacts)
- Access to the repository secrets (Railway dashboard or GitHub Settings > Secrets)

## 1. Generate Node Credentials

```bash
# Generate a strong bearer token for the node
export PIXEL_10_NODE_TOKEN=$(openssl rand -hex 32)

# Generate HMAC secret for webhook signatures
export EDGE_WEBHOOK_SECRET=$(openssl rand -hex 32)
```

Add both values to:
- **Railway Variables** (production): Railway dashboard > Variables
- **GitHub Secrets** (CI): Repository Settings > Secrets and variables > Actions

## 2. Verify Configuration Files

Confirm these config files are present and correct in the repository:

| File | Purpose |
|------|---------|
| `config/edge_nodes/pixel_10.yaml` | Node identity, labels, auth, resource limits |
| `config/health_gates.yaml` | Health checks that gate activation |
| `config/capabilities.yaml` | Allowed capabilities for the Pixel 10 |
| `config/routing/edge_workers.yaml` | Traffic routing rules (20% edge / 80% cloud) |
| `config/webhooks/edge_node_hooks.yaml` | Inbound and outbound webhook definitions |

## 3. Deploy the Cloud-Side Changes

Push the configuration branch and merge the PR so the cloud controller
recognizes the Pixel 10 node identity.

```bash
git checkout feature/pixel-10-edge-node-config
git push origin feature/pixel-10-edge-node-config
# Open and merge the PR via GitHub
```

## 4. Provision the Device

1. Install the edge agent on the Pixel 10.
2. Configure the agent with:
   - `NODE_ID=pixel-10-edge-001`
   - `CONTROLLER_URL=${BASE_URL}`
   - `PIXEL_10_NODE_TOKEN=<the token generated above>`
3. Start the agent service.

## 5. Health Gate Verification

The node starts in `inactive` status. The controller runs these health checks
every 60 seconds (see `config/health_gates.yaml`):

1. **Heartbeat** — `GET /edge/heartbeat` returns `node_id: pixel-10-edge-001`
2. **Auth** — `POST /edge/auth/verify` with the bearer token returns `authenticated: true`
3. **Resources** — CPU < 80%, RAM available > 128 MB, battery > 20%
4. **Network latency** — round-trip under 200 ms

After **3 consecutive passes**, the controller promotes the node to `active`
and fires the `node-activated` webhook.

## 6. Post-Activation Validation

Once the node is active, verify:

- [ ] Node appears in `/metrics` or dashboard with status `active`
- [ ] Heartbeat webhooks arriving at `/webhook/edge/heartbeat`
- [ ] A test task (`health_probe`) can be assigned via `/edge/tasks/assign`
- [ ] Revenue collection events flow to `/webhook/edge/revenue`
- [ ] The GitHub Actions edge-node health check passes

## 7. Monitoring

- **Health**: GitHub Actions `edge-node-health-check.yml` runs daily at 09:15 UTC
- **Resources**: Watchdog (`watchdog.py`) monitors CPU/RAM thresholds
- **Revenue**: `revenue_aggregator.py` includes edge-sourced revenue in MRR tracking
- **Logs**: Edge events are logged under the `[EDGE]` prefix

## Rollback

If the node causes issues:

1. Set `status: inactive` in `config/edge_nodes/pixel_10.yaml` and push.
2. Or remove `pixel-10-edge` from `config/routing/edge_workers.yaml` pools.
3. Traffic automatically falls back to `cloud-primary` per failover rules.

## Reference

- Node config: `config/edge_nodes/pixel_10.yaml`
- Health gates: `config/health_gates.yaml`
- Capabilities: `config/capabilities.yaml`
- Routing: `config/routing/edge_workers.yaml`
- Webhooks: `config/webhooks/edge_node_hooks.yaml`
- API spec: `docs/api/openapi.yaml` (edge endpoints section)
