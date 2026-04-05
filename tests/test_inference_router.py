"""
Tests for the inference routing logic.

Covers: feature gate, task eligibility, health checks, resource constraints,
latency-based routing decisions, fallback, and cooldown.
"""

import time
import pytest
from unittest.mock import patch

from core.inference_router import (
    InferenceRouter,
    InferenceRoutingConfig,
    LatencyTracker,
    EdgeNodeHealth,
    HealthStatus,
    RoutingTarget,
    REVENUE_TASK_TYPES,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    return InferenceRoutingConfig()


@pytest.fixture
def enabled_config(tmp_path):
    """Config with feature gate enabled."""
    cfg_file = tmp_path / "inference_routing.yaml"
    cfg_file.write_text(
        """
inference_routing:
  version: 1
  feature_gate:
    enabled: true
    require_node_active: true
    require_model_ready: true
  latency_thresholds:
    cloud_latency_floor_ms: 150
    edge_latency_ceiling_ms: 500
    edge_reachability_max_ms: 200
    sample_window_size: 10
    min_samples_required: 5
  inference_health_requirements:
    min_battery_pct: 30
    max_cpu_pct: 50
    min_ram_available_mb: 256
    max_concurrent_inference_tasks: 2
    model_warmup_grace_s: 60
  eligible_task_types:
    - task_type: lead_score
      priority: high
      max_edge_latency_ms: 400
      fallback: cloud-gemini
    - task_type: revenue_forecast
      priority: medium
      max_edge_latency_ms: 500
      fallback: cloud-gemini
    - task_type: outreach_personalize
      priority: medium
      max_edge_latency_ms: 450
      fallback: cloud-gemini
  fallback:
    default_target: cloud-gemini
    retry_edge_after_s: 300
  telemetry:
    sources: []
    aggregation:
      method: exponential_moving_average
      alpha: 0.3
"""
    )
    return InferenceRoutingConfig(str(cfg_file))


@pytest.fixture
def router(enabled_config):
    return InferenceRouter(config=enabled_config)


def _make_healthy_edge(router):
    """Set up a healthy edge node state on the router."""
    router.update_edge_health(
        node_active=True,
        model_ready=True,
        battery_pct=80,
        cpu_pct=20.0,
        ram_available_mb=400,
        concurrent_inference_tasks=0,
        heartbeat_rtt_ms=50.0,
    )


def _populate_latency(router, cloud_ms=200.0, edge_ms=100.0, n=6):
    """Record enough latency samples to pass min_samples_required."""
    for _ in range(n):
        router.record_cloud_latency(cloud_ms)
        router.record_edge_latency(edge_ms)


# ── InferenceRoutingConfig ──────────────────────────────────────────────────


class TestInferenceRoutingConfig:

    def test_default_config_gate_disabled(self, config):
        assert config.gate_enabled is False

    def test_enabled_config_gate_enabled(self, enabled_config):
        assert enabled_config.gate_enabled is True

    def test_latency_thresholds(self, enabled_config):
        assert enabled_config.cloud_latency_floor_ms == 150
        assert enabled_config.edge_latency_ceiling_ms == 500
        assert enabled_config.edge_reachability_max_ms == 200

    def test_sample_window(self, enabled_config):
        assert enabled_config.sample_window_size == 10
        assert enabled_config.min_samples_required == 5

    def test_eligible_task_types(self, enabled_config):
        types = enabled_config.eligible_task_types
        assert "lead_score" in types
        assert "revenue_forecast" in types
        assert "outreach_personalize" in types

    def test_get_task_config(self, enabled_config):
        cfg = enabled_config.get_task_config("lead_score")
        assert cfg is not None
        assert cfg["max_edge_latency_ms"] == 400

    def test_get_task_config_missing(self, enabled_config):
        assert enabled_config.get_task_config("data_ingest") is None

    def test_missing_config_file(self, tmp_path):
        cfg = InferenceRoutingConfig(str(tmp_path / "nonexistent.yaml"))
        assert cfg.gate_enabled is False

    def test_health_requirements(self, enabled_config):
        reqs = enabled_config.health_requirements
        assert reqs["min_battery_pct"] == 30
        assert reqs["max_cpu_pct"] == 50
        assert reqs["min_ram_available_mb"] == 256


# ── LatencyTracker ──────────────────────────────────────────────────────────


class TestLatencyTracker:

    def test_empty_tracker(self):
        t = LatencyTracker()
        assert t.average_ms is None
        assert t.sample_count == 0
        assert t.latest_ms is None

    def test_single_sample(self):
        t = LatencyTracker()
        t.record(100.0)
        assert t.average_ms == 100.0
        assert t.sample_count == 1
        assert t.latest_ms == 100.0

    def test_ema_weighting(self):
        t = LatencyTracker(alpha=0.5)
        t.record(100.0)
        t.record(200.0)
        # EMA: 0.5 * 200 + 0.5 * 100 = 150
        assert t.average_ms == 150.0

    def test_window_size_limit(self):
        t = LatencyTracker(window_size=3)
        for v in [10, 20, 30, 40, 50]:
            t.record(v)
        assert t.sample_count == 3

    def test_reset(self):
        t = LatencyTracker()
        t.record(100.0)
        t.reset()
        assert t.average_ms is None
        assert t.sample_count == 0


# ── EdgeNodeHealth ──────────────────────────────────────────────────────────


class TestEdgeNodeHealth:

    def test_initial_state(self):
        h = EdgeNodeHealth()
        assert h.status == HealthStatus.UNHEALTHY
        assert h.node_active is False

    def test_healthy_state(self):
        h = EdgeNodeHealth()
        h.update(
            node_active=True, model_ready=True,
            battery_pct=80, cpu_pct=20.0,
            ram_available_mb=400, heartbeat_rtt_ms=50.0,
        )
        assert h.status == HealthStatus.HEALTHY

    def test_degraded_when_model_not_ready(self):
        h = EdgeNodeHealth()
        h.update(
            node_active=True, model_ready=False,
            battery_pct=80, cpu_pct=20.0,
            ram_available_mb=400,
        )
        assert h.status == HealthStatus.DEGRADED

    def test_unhealthy_when_node_inactive(self):
        h = EdgeNodeHealth()
        h.update(
            node_active=False, model_ready=True,
            battery_pct=80, cpu_pct=20.0,
            ram_available_mb=400,
        )
        assert h.status == HealthStatus.UNHEALTHY

    def test_stale_becomes_unknown(self):
        h = EdgeNodeHealth()
        h.update(
            node_active=True, model_ready=True,
            battery_pct=80, cpu_pct=20.0,
            ram_available_mb=400,
        )
        # Simulate staleness by backdating last_updated
        h.last_updated = time.time() - 200
        assert h.status == HealthStatus.UNKNOWN

    def test_to_dict(self):
        h = EdgeNodeHealth()
        h.update(
            node_active=True, model_ready=True,
            battery_pct=75, cpu_pct=30.0,
            ram_available_mb=350, concurrent_inference_tasks=1,
            heartbeat_rtt_ms=45.0,
        )
        d = h.to_dict()
        assert d["node_active"] is True
        assert d["battery_pct"] == 75
        assert d["health_status"] == "healthy"


# ── InferenceRouter — Feature Gate ──────────────────────────────────────────


class TestRouterFeatureGate:

    def test_gate_disabled_routes_to_cloud(self, config):
        router = InferenceRouter(config=config)
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "feature_gate_disabled"

    def test_gate_enabled_proceeds(self, router):
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=100.0)
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.EDGE.value


# ── InferenceRouter — Task Eligibility ──────────────────────────────────────


class TestRouterTaskEligibility:

    def test_non_revenue_task_routes_to_cloud(self, router):
        _make_healthy_edge(router)
        result = router.route("health_probe")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "task_not_eligible_for_edge_inference"

    def test_data_ingest_routes_to_cloud(self, router):
        _make_healthy_edge(router)
        result = router.route("data_ingest")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "task_not_eligible_for_edge_inference"

    def test_eligible_task_types_constant(self):
        assert REVENUE_TASK_TYPES == {"lead_score", "revenue_forecast", "outreach_personalize"}


# ── InferenceRouter — Health Checks ─────────────────────────────────────────


class TestRouterHealthChecks:

    def test_node_inactive_routes_to_cloud(self, router):
        router.update_edge_health(
            node_active=False, model_ready=True,
            battery_pct=80, cpu_pct=20.0, ram_available_mb=400,
        )
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_node_not_active"

    def test_model_not_ready_routes_to_cloud(self, router):
        router.update_edge_health(
            node_active=True, model_ready=False,
            battery_pct=80, cpu_pct=20.0, ram_available_mb=400,
        )
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_model_not_ready"


# ── InferenceRouter — Resource Constraints ──────────────────────────────────


class TestRouterResourceConstraints:

    def test_low_battery_routes_to_cloud(self, router):
        router.update_edge_health(
            node_active=True, model_ready=True,
            battery_pct=15, cpu_pct=20.0, ram_available_mb=400,
        )
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_battery_too_low"

    def test_high_cpu_routes_to_cloud(self, router):
        router.update_edge_health(
            node_active=True, model_ready=True,
            battery_pct=80, cpu_pct=75.0, ram_available_mb=400,
        )
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_cpu_too_high"

    def test_low_ram_routes_to_cloud(self, router):
        router.update_edge_health(
            node_active=True, model_ready=True,
            battery_pct=80, cpu_pct=20.0, ram_available_mb=100,
        )
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_ram_insufficient"

    def test_max_concurrent_inference_routes_to_cloud(self, router):
        router.update_edge_health(
            node_active=True, model_ready=True,
            battery_pct=80, cpu_pct=20.0, ram_available_mb=400,
            concurrent_inference_tasks=2,
        )
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_at_max_concurrent_inference"

    def test_heartbeat_rtt_too_high_routes_to_cloud(self, router):
        router.update_edge_health(
            node_active=True, model_ready=True,
            battery_pct=80, cpu_pct=20.0, ram_available_mb=400,
            heartbeat_rtt_ms=250.0,
        )
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_heartbeat_latency_too_high"


# ── InferenceRouter — Latency-Based Decisions ───────────────────────────────


class TestRouterLatencyDecisions:

    def test_insufficient_samples_routes_to_cloud(self, router):
        _make_healthy_edge(router)
        # Only 3 samples, need 5
        for _ in range(3):
            router.record_cloud_latency(200.0)
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "insufficient_latency_samples"

    def test_cloud_latency_above_floor_routes_to_edge(self, router):
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=100.0)
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.EDGE.value
        assert result["reason"] == "cloud_latency_above_floor_edge_preferred"

    def test_cloud_latency_below_floor_stays_on_cloud(self, router):
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=100.0, edge_ms=50.0)
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "cloud_latency_acceptable"

    def test_edge_latency_exceeds_ceiling_fallback(self, router):
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=600.0)
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_latency_exceeds_ceiling"

    def test_per_task_latency_ceiling(self, router):
        """lead_score has a stricter ceiling of 400ms."""
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=450.0)
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_latency_exceeds_ceiling"

    def test_revenue_forecast_higher_ceiling(self, router):
        """revenue_forecast has a 500ms ceiling — 450ms edge should work."""
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=450.0)
        result = router.route("revenue_forecast")
        assert result["target"] == RoutingTarget.EDGE.value


# ── InferenceRouter — Fallback Cooldown ─────────────────────────────────────


class TestRouterFallbackCooldown:

    def test_cooldown_after_fallback(self, router):
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=100.0)
        router.record_edge_fallback()
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.CLOUD.value
        assert result["reason"] == "edge_in_fallback_cooldown"

    def test_cooldown_expires(self, router):
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=100.0)
        router.record_edge_fallback()
        # Simulate cooldown expiry
        router._last_edge_fallback_time = time.time() - 400
        result = router.route("lead_score")
        assert result["target"] == RoutingTarget.EDGE.value


# ── InferenceRouter — Latency Data in Response ──────────────────────────────


class TestRouterLatencyData:

    def test_route_includes_latency_data(self, router):
        _make_healthy_edge(router)
        _populate_latency(router, cloud_ms=200.0, edge_ms=100.0)
        result = router.route("lead_score")
        assert "latency_data" in result
        assert result["latency_data"]["cloud_samples"] == 6
        assert result["latency_data"]["edge_samples"] == 6
        assert result["latency_data"]["cloud_avg_ms"] is not None

    def test_route_includes_task_type(self, router):
        result = router.route("lead_score")
        assert result["task_type"] == "lead_score"
