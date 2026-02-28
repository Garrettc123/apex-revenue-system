#!/usr/bin/env python3
import time, datetime, os

LOG = os.path.expanduser("~/.ai_hub.log")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

if __name__ == "__main__":
    log("AI Agent Hub started")
    while True:
        log("AI Hub Heartbeat — Lead scoring, outreach, sequencing: ACTIVE")
        time.sleep(3600)
