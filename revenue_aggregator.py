#!/usr/bin/env python3
import time, datetime, json, os, requests

LOG = os.path.expanduser("~/.revenue.log")
STATE_FILE = os.path.expanduser("~/.revenue_state.json")
PORT = os.environ.get("PORT", "5000")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "total_revenue": 0.0,
        "transactions": [],
        "last_updated": None,
        "api_health_checks": 0,
        "api_health_failures": 0
    }

def save_state(state):
    state["last_updated"] = str(datetime.datetime.utcnow())
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def check_api_health(state):
    try:
        r = requests.get(f"http://localhost:{PORT}/health", timeout=5)
        state["api_health_checks"] += 1
        if r.status_code == 200:
            data = r.json()
            log(f"API Health PASS — gemini={data.get('gemini', 'unknown')}")
            return True
        else:
            state["api_health_failures"] += 1
            log(f"API Health FAIL — status={r.status_code}")
            return False
    except Exception as e:
        state["api_health_failures"] += 1
        log(f"API Health ERROR: {e}")
        return False

def aggregate_cycle(state):
    healthy = check_api_health(state)
    total = state["total_revenue"]
    mrr_target = 5000.00
    report = {
        "cycle_time": str(datetime.datetime.utcnow()),
        "api_healthy": healthy,
        "total_revenue_usd": total,
        "mrr_target_usd": mrr_target,
        "gap_to_target_usd": max(0, mrr_target - total),
        "progress_pct": round((total / mrr_target) * 100, 2),
        "transaction_count": len(state["transactions"]),
        "health_checks": state["api_health_checks"],
        "health_failures": state["api_health_failures"]
    }
    log(f"Revenue Report: {json.dumps(report)}")
    return report

log("=== Revenue Aggregator GENESIS — Online ===")
state = load_state()
cycle = 0
while True:
    cycle += 1
    log(f"--- Aggregation Cycle {cycle} ---")
    state = load_state()
    aggregate_cycle(state)
    save_state(state)
    log(f"Cycle {cycle} complete. Sleeping 1 hour.")
    time.sleep(3600)
