#!/usr/bin/env python3
import time, datetime, os, json
from google import genai as _genai

LOG = os.path.expanduser("~/.ai_hub.log")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def run_gemini(prompt):
    if not GEMINI_API_KEY:
        log("WARNING: GEMINI_API_KEY not set — skipping AI call")
        return None
    try:
        client = _genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text
    except Exception as e:
        log(f"Gemini error: {e}")
        return None

def lead_scoring_cycle():
    log("Running lead scoring cycle...")
    result = run_gemini(
        "You are a B2B SaaS sales AI for an autonomous revenue platform. "
        "Generate 3 high-priority lead profiles. Include: company name, industry, "
        "key pain point, estimated annual deal size, and personalized outreach angle. "
        "Format as JSON array."
    )
    if result:
        log(f"Lead Scoring Complete: {result[:300]}...")
    return result

def email_sequence_cycle():
    log("Generating email outreach sequences...")
    result = run_gemini(
        "Write a 3-email cold outreach sequence for selling an autonomous AI revenue platform "
        "to e-commerce and SaaS businesses. Keep each email under 120 words. "
        "Focus on concrete ROI metrics, time savings, and revenue uplift. "
        "Include subject lines."
    )
    if result:
        log(f"Email Sequences Ready: {result[:300]}...")
    return result

def revenue_strategy_cycle():
    log("Generating revenue strategy...")
    result = run_gemini(
        "You are GENESIS revenue strategist. Analyze top 3 fastest paths to $5000 MRR "
        "for an autonomous AI SaaS platform in 2026. Be specific: pricing, channels, "
        "customer acquisition cost estimates, and 30-day action steps."
    )
    if result:
        log(f"Revenue Strategy: {result[:300]}...")
    return result

log("=== AI Agent Hub GENESIS — Live Gemini Activated ===")
cycle = 0
while True:
    cycle += 1
    log(f"--- Agent Cycle {cycle} ---")
    lead_scoring_cycle()
    if cycle % 3 == 0:
        email_sequence_cycle()
    if cycle % 6 == 0:
        revenue_strategy_cycle()
    log(f"Cycle {cycle} complete. Sleeping 1 hour.")
    time.sleep(3600)
