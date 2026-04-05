"""
Inference Router — Routes revenue-relevant inference tasks to the Pixel 10
edge node (Qwen2.5) or cloud (Gemini) based on latency telemetry and health.

Feature-gated: all routing falls back to cloud when the gate is closed.
"""

import time
import os
from collections import deque
from enum import Enum
from typing import Optional

import yaml


CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
INFERENCE_ROUTING_CONFIG = os.path.join(CONFIG_DIR, "routing", "inference_routing.yaml")

REVENUE_TASK_TYPES = frozenset({"lead_score", "revenue_forecast", "outreach_personalize"})


class RoutingTarget(Enum):
    CLOUD = "cloud-gemini"
    EDGE = "pixel-10-edge-qwen25"


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class InferenceRoutingConfig:
    """Loads and exposes inference routing configuration."""

    def __init__(self, config_path: str = INFERENCE_ROUTING_CONFIG):
        self._config_path = config_path
        self._config = self._load()

    def _load(self) -> dict:
        try:
            with open(self._config_path) as f:
                return yaml.safe_load(f).get("inference_routing", {})
        except (FileNotFoundError, yaml.YAMLError):
            return {}

    def reload(self):
        self._config = self._load()

    @property
    def gate_enabled(self) -> bool:
        return self._config.get("feature_gate", {}).get("enabled", False)

    @property
    def require_node_active(self) -> bool:
        return self._config.get("feature_gate", {}).get("require_node_active", True)

    @property
    def require_model_ready(self) -> bool:
        return self._config.get("feature_gate", {}).get("require_model_ready", True)

    @property
    def latency_thresholds(self) -> dict:
        return self._config.get("latency_thresholds", {})

    @property
    def cloud_latency_floor_ms(self) -> int:
        return self.latency_thresholds.get("cloud_latency_floor_ms", 150)

    @property
    def edge_latency_ceiling_ms(self) -> int:
        return self.latency_thresholds.get("edge_latency_ceiling_ms", 500)

    @property
    def edge_reachability_max_ms(self) -> int:
        return self.latency_thresholds.get("edge_reachability_max_ms", 200)

    @property
    def sample_window_size(self) -> int:
        return self.latency_thresholds.get("sample_window_size", 10)

    @property
    def min_samples_required(self) -> int:
        return self.latency_thresholds.get("min_samples_required", 5)

    @property
    def health_requirements(self) -> dict:
        return self._config.get("inference_health_requirements", {})

    @property
    def eligible_task_types(self) -> list:
        return [
            t["task_type"]
            for t in self._config.get("eligible_task_types", [])
        ]

    @property
    def fallback_target(self) -> str:
        return self._config.get("fallback", {}).get("default_target", "cloud-gemini")

    @property
    def retry_edge_after_s(self) -> int:
        return self._config.get("fallback", {}).get("retry_edge_after_s", 300)

    def get_task_config(self, task_type: str) -> Optional[dict]:
        for t in self._config.get("eligible_task_types", []):
            if t["task_type"] == task_type:
                return t
        return None


class LatencyTracker:
    """Tracks latency samples using an exponential moving average."""

    def __init__(self, window_size: int = 10, alpha: float = 0.3):
        self._window_size = window_size
        self._alpha = alpha
        self._samples: deque = deque(maxlen=window_size)
        self._ema: Optional[float] = None

    def record(self, latency_ms: float):
        self._samples.append(latency_ms)
        if self._ema is None:
            self._ema = latency_ms
        else:
            self._ema = self._alpha * latency_ms + (1 - self._alpha) * self._ema

    @property
    def average_ms(self) -> Optional[float]:
        return self._ema

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def latest_ms(self) -> Optional[float]:
        return self._samples[-1] if self._samples else None

    def reset(self):
        self._samples.clear()
        self._ema = None


class EdgeNodeHealth:
    """Represents the current health state of the Pixel 10 edge node."""

    def __init__(self):
        self.node_active: bool = False
        self.model_ready: bool = False
        self.battery_pct: int = 0
        self.cpu_pct: float = 0.0
        self.ram_available_mb: int = 0
        self.concurrent_inference_tasks: int = 0
        self.last_heartbeat_rtt_ms: Optional[float] = None
        self.last_updated: float = 0.0

    def update(
        self,
        node_active: bool,
        model_ready: bool,
        battery_pct: int,
        cpu_pct: float,
        ram_available_mb: int,
        concurrent_inference_tasks: int = 0,
        heartbeat_rtt_ms: Optional[float] = None,
    ):
        self.node_active = node_active
        self.model_ready = model_ready
        self.battery_pct = battery_pct
        self.cpu_pct = cpu_pct
        self.ram_available_mb = ram_available_mb
        self.concurrent_inference_tasks = concurrent_inference_tasks
        self.last_heartbeat_rtt_ms = heartbeat_rtt_ms
        self.last_updated = time.time()

    @property
    def status(self) -> HealthStatus:
        if not self.node_active:
            return HealthStatus.UNHEALTHY
        if not self.model_ready:
            return HealthStatus.DEGRADED
        stale = (time.time() - self.last_updated) > 120
        if stale:
            return HealthStatus.UNKNOWN
        return HealthStatus.HEALTHY

    def to_dict(self) -> dict:
        return {
            "node_active": self.node_active,
            "model_ready": self.model_ready,
            "battery_pct": self.battery_pct,
            "cpu_pct": self.cpu_pct,
            "ram_available_mb": self.ram_available_mb,
            "concurrent_inference_tasks": self.concurrent_inference_tasks,
            "last_heartbeat_rtt_ms": self.last_heartbeat_rtt_ms,
            "health_status": self.status.value,
        }


class InferenceRouter:
    """Decides whether to route an inference task to edge (Qwen2.5) or cloud (Gemini)."""

    def __init__(self, config: Optional[InferenceRoutingConfig] = None):
        self._config = config or InferenceRoutingConfig()
        self._cloud_latency = LatencyTracker(
            window_size=self._config.sample_window_size,
            alpha=0.3,
        )
        self._edge_latency = LatencyTracker(
            window_size=self._config.sample_window_size,
            alpha=0.3,
        )
        self._edge_health = EdgeNodeHealth()
        self._last_edge_fallback_time: float = 0.0

    @property
    def config(self) -> InferenceRoutingConfig:
        return self._config

    @property
    def edge_health(self) -> EdgeNodeHealth:
        return self._edge_health

    def record_cloud_latency(self, latency_ms: float):
        self._cloud_latency.record(latency_ms)

    def record_edge_latency(self, latency_ms: float):
        self._edge_latency.record(latency_ms)

    def update_edge_health(self, **kwargs):
        self._edge_health.update(**kwargs)

    def route(self, task_type: str) -> dict:
        """Decide where to route an inference task.

        Returns a dict with:
          - target: RoutingTarget value string
          - reason: human-readable explanation
          - task_type: the input task type
          - latency_data: current latency telemetry snapshot
        """
        result = {
            "task_type": task_type,
            "latency_data": self._latency_snapshot(),
        }

        # 1. Feature gate check
        if not self._config.gate_enabled:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "feature_gate_disabled"}

        # 2. Task type eligibility
        if task_type not in REVENUE_TASK_TYPES:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "task_not_eligible_for_edge_inference"}

        if task_type not in self._config.eligible_task_types:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "task_type_not_in_config"}

        # 3. Edge node health check
        if self._config.require_node_active and not self._edge_health.node_active:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_node_not_active"}

        if self._config.require_model_ready and not self._edge_health.model_ready:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_model_not_ready"}

        if self._edge_health.status == HealthStatus.UNHEALTHY:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_node_unhealthy"}

        # 4. Resource constraints
        health_reqs = self._config.health_requirements
        if self._edge_health.battery_pct < health_reqs.get("min_battery_pct", 30):
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_battery_too_low"}

        if self._edge_health.cpu_pct > health_reqs.get("max_cpu_pct", 50):
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_cpu_too_high"}

        if self._edge_health.ram_available_mb < health_reqs.get("min_ram_available_mb", 256):
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_ram_insufficient"}

        max_concurrent = health_reqs.get("max_concurrent_inference_tasks", 2)
        if self._edge_health.concurrent_inference_tasks >= max_concurrent:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_at_max_concurrent_inference"}

        # 5. Reachability check via heartbeat RTT
        if self._edge_health.last_heartbeat_rtt_ms is not None:
            if self._edge_health.last_heartbeat_rtt_ms > self._config.edge_reachability_max_ms:
                return {**result, "target": RoutingTarget.CLOUD.value,
                        "reason": "edge_heartbeat_latency_too_high"}

        # 6. Cooldown after previous fallback
        if self._last_edge_fallback_time > 0:
            elapsed = time.time() - self._last_edge_fallback_time
            if elapsed < self._config.retry_edge_after_s:
                return {**result, "target": RoutingTarget.CLOUD.value,
                        "reason": "edge_in_fallback_cooldown"}

        # 7. Latency-based routing decision
        if self._cloud_latency.sample_count < self._config.min_samples_required:
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "insufficient_latency_samples"}

        cloud_avg = self._cloud_latency.average_ms
        edge_avg = self._edge_latency.average_ms

        # Check per-task latency ceiling
        task_cfg = self._config.get_task_config(task_type)
        task_edge_ceiling = (
            task_cfg.get("max_edge_latency_ms", self._config.edge_latency_ceiling_ms)
            if task_cfg else self._config.edge_latency_ceiling_ms
        )

        # If we have edge latency data and it exceeds the ceiling, fall back
        if edge_avg is not None and edge_avg > task_edge_ceiling:
            self._last_edge_fallback_time = time.time()
            return {**result, "target": RoutingTarget.CLOUD.value,
                    "reason": "edge_latency_exceeds_ceiling"}

        # Route to edge when cloud latency is above the floor threshold
        if cloud_avg is not None and cloud_avg > self._config.cloud_latency_floor_ms:
            return {**result, "target": RoutingTarget.EDGE.value,
                    "reason": "cloud_latency_above_floor_edge_preferred"}

        # Default: cloud is fast enough, stay on cloud
        return {**result, "target": RoutingTarget.CLOUD.value,
                "reason": "cloud_latency_acceptable"}

    def record_edge_fallback(self):
        """Call when an edge inference fails and falls back to cloud."""
        self._last_edge_fallback_time = time.time()

    def _latency_snapshot(self) -> dict:
        return {
            "cloud_avg_ms": self._cloud_latency.average_ms,
            "cloud_samples": self._cloud_latency.sample_count,
            "edge_avg_ms": self._edge_latency.average_ms,
            "edge_samples": self._edge_latency.sample_count,
            "edge_heartbeat_rtt_ms": self._edge_health.last_heartbeat_rtt_ms,
        }
