from flask import Flask, jsonify, request, render_template, redirect
import datetime, os, json, hmac, hashlib
import requests as http
from google import genai as _genai

app = Flask(__name__)

# --- Config ---
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
CB_API_KEY         = os.environ.get("COINBASE_API_KEY", "")
CB_WEBHOOK_SECRET  = os.environ.get("COINBASE_WEBHOOK_SECRET", "")
BASE_URL           = os.environ.get("BASE_URL", "https://apex-revenue-system.up.railway.app")
REVENUE_LEDGER_FILE = "/tmp/revenue_ledger.json"
DEFAULT_MRR_TARGET_USD = 5000.0

CB_API_URL = "https://api.commerce.coinbase.com"
CB_HEADERS = {
    "X-CC-Api-Key": CB_API_KEY,
    "X-CC-Version": "2018-03-22",
    "Content-Type": "application/json",
}

if GEMINI_API_KEY:
    _gemini_client = _genai.Client(api_key=GEMINI_API_KEY)
else:
    _gemini_client = None

PRICING = {
    "starter":    {"amount": "49.00",  "name": "GENESIS Starter",    "label": "$49/mo"},
    "pro":        {"amount": "149.00", "name": "GENESIS Pro",        "label": "$149/mo"},
    "enterprise": {"amount": "499.00", "name": "GENESIS Enterprise", "label": "$499/mo"},
}

# --- Revenue Ledger ---
def load_ledger():
    if os.path.exists(REVENUE_LEDGER_FILE):
        try:
            with open(REVENUE_LEDGER_FILE) as f:
                ledger = json.load(f)
                if isinstance(ledger, dict):
                    events = ledger.get("events", [])
                    target = ledger.get("mrr_target_usd", DEFAULT_MRR_TARGET_USD)
                    return {
                        "events": events if isinstance(events, list) else [],
                        "mrr_target_usd": float(target) if target is not None else DEFAULT_MRR_TARGET_USD,
                    }
        except Exception:
            pass
    return {"events": [], "mrr_target_usd": DEFAULT_MRR_TARGET_USD}


def record_revenue_event(event_type, amount_usd, plan, charge_id, source, status, extra=None):
    ledger = load_ledger()
    event = {
        "event_type": event_type,
        "amount_usd": float(amount_usd),
        "plan": plan,
        "charge_id": charge_id,
        "source": source,
        "status": status,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if extra:
        event.update(extra)
    ledger["events"].append(event)
    with open(REVENUE_LEDGER_FILE, "w") as f:
        json.dump(ledger, f, indent=2)

# --- Core Routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "time": str(datetime.datetime.now(datetime.timezone.utc)),
        "gemini": "connected" if _gemini_client else "set GEMINI_API_KEY",
        "coinbase": "connected" if CB_API_KEY else "set COINBASE_API_KEY"
    })

@app.route("/metrics")
def metrics():
    ledger = load_ledger()
    events = ledger.get("events", [])
    active = [e for e in events if e.get("status") == "confirmed"]
    mrr = sum(float(e.get("amount_usd", 0) or 0) for e in active)
    mrr_target = float(ledger.get("mrr_target_usd", DEFAULT_MRR_TARGET_USD) or DEFAULT_MRR_TARGET_USD)
    return jsonify({
        "mrr_usd": mrr,
        "mrr_target_usd": mrr_target,
        "progress_pct": round((mrr / mrr_target) * 100, 2) if mrr_target else 0,
        "active_customers": len(active),
        "total_customers": len(events),
        "total_events": len(events),
        "gap_to_target": max(0, mrr_target - mrr),
    })

# --- Coinbase Commerce Checkout ---
@app.route("/checkout/<plan>")
def checkout(plan):
    if not CB_API_KEY:
        return jsonify({"error": "Coinbase not configured. Set COINBASE_API_KEY in Railway Variables."}), 503
    if plan not in PRICING:
        return redirect("/")
    p = PRICING[plan]
    try:
        payload = {
            "name": p["name"],
            "description": f"Apex Revenue System — Autonomous AI Platform ({p['label']})",
            "pricing_type": "fixed_price",
            "local_price": {"amount": p["amount"], "currency": "USD"},
            "metadata": {"plan": plan, "amount": p["amount"]},
            "redirect_url": f"{BASE_URL}/success",
            "cancel_url": f"{BASE_URL}/?cancelled=1",
        }
        resp = http.post(f"{CB_API_URL}/charges", headers=CB_HEADERS, json=payload, timeout=10)
        resp.raise_for_status()
        hosted_url = resp.json()["data"]["hosted_url"]
        return redirect(hosted_url, code=303)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/success")
def success():
    return render_template("success.html")

# --- Coinbase Webhook ---
@app.route("/webhook/coinbase", methods=["POST"])
def coinbase_webhook():
    payload = request.data
    sig = request.headers.get("X-CC-Webhook-Signature", "")

    if CB_WEBHOOK_SECRET:
        computed = hmac.new(
            CB_WEBHOOK_SECRET.encode("utf-8"),
            payload,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(computed, sig):
            return jsonify({"error": "Invalid signature"}), 400

    try:
        event = json.loads(payload)
        etype = event.get("event", {}).get("type", "")
        data  = event.get("event", {}).get("data", {})

        if etype == "charge:confirmed":
            meta = data.get("metadata", {})
            pricing = data.get("pricing", {}).get("local", {})
            try:
                amount_usd = float(pricing.get("amount", 0))
            except (TypeError, ValueError):
                amount_usd = 0.0
            record_revenue_event(
                event_type="charge:confirmed",
                amount_usd=amount_usd,
                plan=meta.get("plan", "unknown"),
                charge_id=data.get("id", ""),
                source="coinbase",
                status="confirmed",
                extra={"currency": pricing.get("currency", "USD")},
            )
        elif etype == "charge:failed":
            print(f"Charge failed: {data.get('id','')}")
        elif etype == "charge:pending":
            print(f"Charge pending: {data.get('id','')}")
    except Exception as e:
        print(f"Webhook parse error: {e}")

    return jsonify({"status": "ok"})


@app.route("/webhook/edge/revenue", methods=["POST"])
def edge_revenue_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    required = ("node_id", "charge_id", "amount_usd", "plan", "source")
    missing = [field for field in required if field not in payload]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400
    try:
        amount_usd = float(payload.get("amount_usd"))
    except (TypeError, ValueError):
        return jsonify({"error": "amount_usd must be numeric"}), 400
    record_revenue_event(
        event_type="revenue-collected",
        amount_usd=amount_usd,
        plan=payload.get("plan", "unknown"),
        charge_id=payload.get("charge_id", ""),
        source="edge",
        status="confirmed",
        extra={
            "node_id": payload.get("node_id"),
            "timestamp": payload.get("timestamp"),
            "reported_source": payload.get("source"),
        },
    )
    return jsonify({"status": "recorded"})


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    event_type = payload.get("type", "")
    obj = payload.get("data", {}).get("object", {})
    if event_type in ("payment_intent.succeeded", "charge.succeeded"):
        amount_cents = obj.get("amount_received", obj.get("amount", 0))
        try:
            amount_usd = float(amount_cents) / 100.0
        except (TypeError, ValueError):
            amount_usd = 0.0
        metadata = obj.get("metadata", {})
        record_revenue_event(
            event_type=event_type,
            amount_usd=amount_usd,
            plan=metadata.get("plan", "unknown"),
            charge_id=obj.get("id", ""),
            source="stripe",
            status="confirmed",
            extra={"currency": obj.get("currency", "usd")},
        )
    return jsonify({"status": "ok"})

# --- AI Endpoints ---
@app.route("/genesis", methods=["GET", "POST"])
def genesis():
    if not _gemini_client:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
    prompt = (request.get_json(force=True, silent=True) or {}).get("prompt",
        "You are GENESIS. Provide 3 specific strategies to reach $5000 MRR in 30 days "
        "for an autonomous AI SaaS platform. Include pricing, acquisition channels, and exact steps.")
    try:
        resp = _gemini_client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return jsonify({"genesis_output": resp.text, "timestamp": str(datetime.datetime.now(datetime.timezone.utc))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ai/leads")
def ai_leads():
    if not _gemini_client:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
    try:
        resp = _gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=(
                "Generate 5 high-value B2B SaaS lead profiles for an autonomous AI revenue platform. "
                "Include: company, industry, pain point, deal size USD/mo, outreach angle. Return JSON array."
            ))
        return jsonify({"leads": resp.text, "timestamp": str(datetime.datetime.now(datetime.timezone.utc))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ai/analyze", methods=["POST"])
def ai_analyze():
    if not _gemini_client:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
    prompt = (request.get_json(force=True, silent=True) or {}).get("prompt", "Analyze this business.")
    try:
        resp = _gemini_client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return jsonify({"analysis": resp.text, "timestamp": str(datetime.datetime.now(datetime.timezone.utc))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
