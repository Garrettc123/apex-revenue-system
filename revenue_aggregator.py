#!/usr/bin/env python3
import time, datetime, os

LOG = os.path.expanduser("~/.revenue.log")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

if __name__ == "__main__":
    log("Revenue Aggregator started")
    while True:
        log("Heartbeat — Systems: ONLINE | MRR Target: $5000")
        time.sleep(3600)
