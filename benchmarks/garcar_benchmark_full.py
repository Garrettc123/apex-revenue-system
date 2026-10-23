import argparse
import hashlib
import hmac
import itertools
import json
import math
import os
import platform
import random
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SECRET = b"benchmark-only-not-a-production-secret"
BODY = b'{"id":"benchmark-event","object":"event"}'

def sign(body, timestamp):
    return hmac.new(SECRET, str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()

def verify(body, header, now):
    try:
        fields = [part.split("=", 1) for part in header.split(",")]
        timestamp = int(next(v for k, v in fields if k == "t"))
        signatures = [v for k, v in fields if k == "v1"]
        return abs(now - timestamp) <= 300 and any(hmac.compare_digest(sign(body, timestamp), s) for s in signatures)
    except (ValueError, StopIteration, TypeError):
        return False

RULES = [
    ("NOT_INTERESTED", r"\b(unsubscribe|stop|remove|no thanks)\b"),
    ("HUMAN_ESCALATION", r"\b(owner|garrett|manager|human|representative)\b"),
    ("OBJECTION_PRIVACY", r"\b(privacy|data|nda|security|compliance)\b"),
    ("OBJECTION_PRICE", r"\b(expensive|cost|budget|price|discount)\b"),
    ("WANTS_CALL", r"\b(call|phone|schedule|zoom|talk|calendar)\b"),
    ("BUY_AUDIT", r"\b(audit|leak|sign up|start|checkout)\b|(?<![\d,])47(?![\d,])"),
]
RULES = [(intent, re.compile(pattern, re.I)) for intent, pattern in RULES]

def classify(text):
    return next((intent for intent, pattern in RULES if pattern.search(text)), "UNCLASSIFIED")

def cost(matrix, route):
    return sum(matrix[route[i]][route[(i + 1) % len(route)]] for i in range(len(route)))

def route_search(matrix):
    route = list(range(len(matrix)))
    while True:
        baseline = cost(matrix, route)
        candidates = [route[:i] + route[i:j + 1][::-1] + route[j + 1:] for i in range(1, len(route) - 1) for j in range(i + 1, len(route))]
        candidate = min(candidates, key=lambda r: cost(matrix, r))
        if cost(matrix, candidate) >= baseline:
            return route
        route = candidate

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def main():
    parser = argparse.ArgumentParser(description="Isolated reference benchmarks; not production integration tests")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", default="benchmark-results")
    args = parser.parse_args()
    require(args.iterations > 0 and args.repeats > 0, "Counts must be positive")
    now = int(time.time())
    header = f"t={now},v1={sign(BODY, now)}"
    require(verify(BODY, header, now), "Valid signature rejected")
    for body, hdr, clock in [(BODY + b" ", header, now), (BODY, f"t={now},v1=bad", now), (BODY, header, now + 301), (BODY, "invalid", now)]:
        require(not verify(body, hdr, clock), "Invalid signature accepted")
    cases = [("Run the $47 lead leak audit", "BUY_AUDIT"), ("Schedule a call", "WANTS_CALL"), ("CRM data under NDA", "OBJECTION_PRIVACY"), ("$1,497 exceeds our budget", "OBJECTION_PRICE"), ("Please remove us", "NOT_INTERESTED"), ("Speak with Garrett", "HUMAN_ESCALATION"), ("Stop sending audit offers", "NOT_INTERESTED"), ("1497", "UNCLASSIFIED")]
    for text, expected in cases:
        require(classify(text) == expected, f"Wrong intent: {text}")
    block = os.urandom(65536)
    expected_hash = hashlib.sha256(block).hexdigest()
    rng = random.Random(42)
    matrix = [[rng.randint(5, 95) if i != j else 0 for j in range(8)] for i in range(8)]
    route = route_search(matrix)
    require(sorted(route) == list(range(8)), "Invalid route")
    require(cost(matrix, route) <= cost(matrix, list(range(8))), "Route worsened")
    optimum = min(cost(matrix, [0] + list(p)) for p in itertools.permutations(range(1, 8)))
    state = {"lead_id": "benchmark-only", "icp_score": 92, "trace": ["ingest", "review"]}
    def checkpoint():
        raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        require(json.loads(raw) == state and len(digest) == 64, "Round-trip failed")
        return raw
    with tempfile.TemporaryDirectory() as temp:
        db_path = str(Path(temp) / "checkpoint.sqlite3")
        db = sqlite3.connect(db_path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE checkpoints (id INTEGER PRIMARY KEY, payload BLOB)")
        def persisted():
            raw = checkpoint()
            db.execute("INSERT OR REPLACE INTO checkpoints VALUES (1, ?)", (raw,))
            db.commit()
            require(db.execute("SELECT payload FROM checkpoints WHERE id=1").fetchone()[0] == raw, "Persistence failed")
        suites = {
            "hmac_reference_header_verification": lambda: require(verify(BODY, header, now), "Verification failed"),
            "sha256_64kib_cached_buffer": lambda: require(hashlib.sha256(block).hexdigest() == expected_hash, "Hash mismatch"),
            "json_state_roundtrip_hash": checkpoint,
            "sqlite_checkpoint_full_sync": persisted,
            "rule_classifier_8_case_batch": lambda: [require(classify(t) == e, "Intent mismatch") for t, e in cases],
            "eight_node_local_search": lambda: route_search(matrix),
        }
        results = {}
        for name, fn in suites.items():
            for _ in range(20):
                fn()
            trials = []
            for _ in range(args.repeats):
                samples = []
                start = time.perf_counter_ns()
                for _ in range(args.iterations):
                    t0 = time.perf_counter_ns()
                    fn()
                    samples.append((time.perf_counter_ns() - t0) / 1e6)
                elapsed = (time.perf_counter_ns() - start) / 1e9
                ordered = sorted(samples)
                trials.append({"mean_ms": statistics.mean(samples), "p95_ms": ordered[math.ceil(.95 * len(ordered)) - 1], "p99_ms": ordered[math.ceil(.99 * len(ordered)) - 1], "ops_sec": args.iterations / elapsed, "samples_ms": samples})
            results[name] = trials
        persisted()
        db.close()
        with sqlite3.connect(db_path) as restored:
            require(json.loads(restored.execute("SELECT payload FROM checkpoints WHERE id=1").fetchone()[0]) == state, "Reopen recovery failed")
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "scope": "isolated reference operations; not production integrations or service capacity", "environment": {"python": sys.version, "platform": platform.platform(), "processor": platform.processor(), "cpu_count": os.cpu_count(), "revision": os.getenv("GITHUB_SHA", "unrecorded")}, "parameters": vars(args), "correctness": "passed", "routing": {"local_cost": cost(matrix, route), "exact_optimum": optimum}, "results": results}
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"correctness": report["correctness"], "results": {name: {"median_trial_mean_ms": statistics.median(t["mean_ms"] for t in trials)} for name, trials in results.items()}}, indent=2))

if __name__ == "__main__":
    import sys
    main()
