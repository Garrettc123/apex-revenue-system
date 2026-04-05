# Qwen2.5 Edge Inference Routing — Setup Guide

This document describes how to enable latency-based inference routing to the
Pixel 10 edge node running Qwen2.5 for revenue-relevant tasks.

## Overview

When enabled, the inference router directs revenue-relevant AI tasks (lead
scoring, revenue forecasting, outreach personalization) to the on-device
Qwen2.5 7B model on the Pixel 10 instead of cloud Gemini — but only when
latency telemetry shows the edge path is faster and the node is healthy.

The feature is **gated by default**. Nothing routes to edge inference until
both conditions are met:

1. The Pixel 10 edge node is `ACTIVE` (passes health gates)
2. The Qwen2.5 model is deployed and reports `ready` status

## Architecture

```
                   ┌────────────────────────┐
                   │   Orchestrator / API    │
                   │                        │
                   │  ┌──────────────────┐  │
 Task Request ────►│  │ InferenceRouter  │  │
                   │  │  (feature-gated) │  │
                   │  └──────┬───────────┘  │
                   │         │              │
                   │    ┌────┴────┐         │
                   │    │ Latency │         │
                   │    │ Check   │         │
                   │    └────┬────┘         │
                   │    ┌────┴────┐         │
                   │    ▼         ▼         │
                   │  Cloud     Edge        │
                   │  Gemini    Qwen2.5     │
                   └────────────────────────┘
```

## Prerequisites

- Pixel 10 edge node fully onboarded (see `onboarding_pixel_10.md`)
- Node passing all health gates and in `ACTIVE` status
- Qwen2.5 7B (Q4_K_M) model deployed on-device via llama.cpp runtime
- Model responding at `/edge/models/status` with `status: ready`

## Configuration Files

| File | Purpose |
|------|---------|
| `config/routing/inference_routing.yaml` | Master config: feature gate, latency thresholds, task types |
| `config/edge_nodes/pixel_10.yaml` | Node config with `inference_models` section |
| `config/capabilities.yaml` | `edge_inference` capability definition |
| `config/health_gates.yaml` | `inference-model-ready` health check |
| `config/routing/edge_workers.yaml` | Latency-aware routing rules for inference tasks |
| `config/webhooks/edge_node_hooks.yaml` | Inference routing event hooks |

## Enabling the Feature

### Step 1: Deploy Qwen2.5 on the Pixel 10

Install and start the Qwen2.5 model on the edge device. The model must
respond to the readiness probe:

```
GET /edge/models/status
→ 200 OK
{
  "models": [{
    "id": "qwen2.5-7b",
    "status": "ready",
    "ram_usage_mb": 248
  }]
}
```

### Step 2: Verify Health Gate

The `inference-model-ready` health check in `config/health_gates.yaml` will
automatically validate that Qwen2.5 is loaded and under RAM limits. This
check is skipped while the feature gate is disabled.

### Step 3: Enable the Feature Gate

In `config/routing/inference_routing.yaml`, set:

```yaml
feature_gate:
  enabled: true
```

Commit and deploy. The inference router will begin evaluating latency
telemetry for routing decisions.

## How Routing Decisions Work

The `InferenceRouter` evaluates each inference request through this chain:

1. **Feature gate** — Is `enabled: true`? If not, cloud.
2. **Task eligibility** — Is the task type in `eligible_task_types`? If not, cloud.
3. **Node health** — Is the edge node `ACTIVE` with model `ready`? If not, cloud.
4. **Resource constraints** — Battery >= 30%, CPU <= 50%, RAM >= 256MB, concurrent tasks < 2? If not, cloud.
5. **Reachability** — Heartbeat RTT <= 200ms? If not, cloud.
6. **Fallback cooldown** — Were we recently forced to fall back? If yes, cloud (5-minute cooldown).
7. **Latency samples** — Do we have at least 5 samples? If not, cloud (not enough data).
8. **Edge latency ceiling** — Is edge latency under the per-task ceiling? If not, cloud.
9. **Cloud latency floor** — Is cloud latency > 150ms? If yes, prefer edge. If no, stay on cloud.

## Latency Thresholds

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `cloud_latency_floor_ms` | 150 | Route to edge only when cloud is slower than this |
| `edge_latency_ceiling_ms` | 500 | Global max acceptable edge latency |
| `edge_reachability_max_ms` | 200 | Max heartbeat RTT for edge to be considered reachable |
| `sample_window_size` | 10 | Number of recent latency samples to track |
| `min_samples_required` | 5 | Minimum samples before making routing decisions |

Per-task ceilings override the global `edge_latency_ceiling_ms`:

| Task | Ceiling |
|------|---------|
| `lead_score` | 400ms |
| `revenue_forecast` | 500ms |
| `outreach_personalize` | 450ms |

## Eligible Task Types

Only revenue-relevant inference tasks are routed to edge:

| Task Type | Description |
|-----------|-------------|
| `lead_score` | AI lead scoring |
| `revenue_forecast` | Short-horizon revenue predictions |
| `outreach_personalize` | Outreach email content personalization |

Non-revenue tasks (`health_probe`, `data_ingest`, `genesis_strategy`) always
stay on cloud regardless of the feature gate.

## Fallback Behavior

If edge inference is unavailable or underperforming, the router automatically
falls back to cloud Gemini. Reasons include:

- Feature gate disabled
- Edge node not active or model not ready
- Resource constraints violated (battery, CPU, RAM)
- Edge latency exceeds ceiling
- Fallback cooldown active (5 minutes after a failure)

After fallback, the router waits `retry_edge_after_s` (300 seconds) before
attempting edge routing again.

## Monitoring

- **Webhooks**: `inference-routed-to-edge`, `inference-fallback-to-cloud`, and
  `inference-model-status-changed` events fire on routing decisions
- **Health gate**: The `inference-model-ready` check runs every 60 seconds
  when the gate is enabled
- **Latency telemetry**: Sourced from orchestrator health checks (60s),
  heartbeat RTT (30s), and actual inference response times

## Disabling the Feature

Set `feature_gate.enabled: false` in `config/routing/inference_routing.yaml`.
All inference tasks will immediately route to cloud Gemini. No edge tasks
will be dispatched.

## Reference

- Inference router module: `core/inference_router.py`
- Tests: `tests/test_inference_router.py`
- Master config: `config/routing/inference_routing.yaml`
- Pixel 10 onboarding: `docs/edge_nodes/onboarding_pixel_10.md`
