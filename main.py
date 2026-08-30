from flask import Flask, jsonify, request, render_template, redirect
import datetime, os, json, hmac, hashlib
import requests as http
import google.generativeai as genai

app = Flask(__name__)

# --- Config ---
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
CB_API_KEY         = os.environ.get("COINBASE_API_KEY", "")
CB_WEBHOOK_SECRET  = os.environ.get("COINBASE_WEBHOOK_SECRET", "")
ENTERPRISE_SYNC_SECRET = os.environ.get("ENTERPRISE_SYNC_SECRET", "super-secret-key")
BASE_URL           = os.environ.get("BASE_URL", "https://apex-revenue-system.up.railway.app")
CUSTOMERS_FILE     = "/tmp/customers.json"

CB_API_URL = "https://api.commerce.coinbase.com"
CB_HEADERS = {
    "X-CC-Api-Key": CB_API_KEY,
    "X-CC-Version": "2018-03-22",
    "Content-Type": "application/json",
}

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

PRICING = {
    "starter":    {"amount": "49.00",  "name": "GENESIS Starter",    "label": "$49/mo"},
    "pro":        {"amount": "149.00", "name": "GENESIS Pro",        "label": "$149/mo"},
    "enterprise": {"amount": "499.00", "name": "GENESIS Enterprise", "label": "$499/mo"},
}

# --- Customer Tracking ---
def load_customers():
    if os.path.exists(CUSTOMERS_FILE):
        try:
            with open(CUSTOMERS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_customer(data):
    customers = load_customers()
    customers.append({**data, "created": str(datetime.datetime.utcnow())})
    with open(CUSTOMERS_FILE, "w") as f:
        json.dump(customers, f, indent=2)

# --- Core Routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "time": str(datetime.datetime.utcnow()),
        "gemini": "connected" if model else "set GEMINI_API_KEY",
        "coinbase": "connected" if CB_API_KEY else "set COINBASE_API_KEY"
    })

@app.route("/metrics")
def metrics():
    customers = load_customers()
    active = [c for c in customers if c.get("status") == "confirmed"]
    mrr = sum(float(c.get("amount", 0)) for c in active)
    return jsonify({
        "mrr_usd": mrr,
        "mrr_target_usd": 5000,
        "progress_pct": round((mrr / 5000) * 100, 2),
        "active_customers": len(active),
        "total_customers": len(customers),
    })

# --- Coinbase Commerce ---
@app.route("/checkout/<plan>")
def checkout(plan):
    if plan not in PRICING:
        return jsonify({"error": "Invalid plan"}), 400
    plan_data = PRICING[plan]
    payload = {
        "name": plan_data["name"],
        "description": f"GENESIS {plan} - Autonomous Revenue AI",
        "pricing_type": "fixed_price",
        "local_price": {"amount": plan_data["amount"], "currency": "USD"},
        "requested_info": ["name", "email"],
        "redirect_url": f"{BASE_URL}/success/{plan}",
        "cancel_url": f"{BASE_URL}/cancel",
    }
    resp = http.post(f"{CB_API_URL}/charges", json=payload, headers=CB_HEADERS)
    data = resp.json()
    if not resp.ok:
        return jsonify({"error": data.get("error", {}).get("message", "Coinbase error")}), 500
    checkout_url = data.get("data", {}).get("hosted_url", "")
    return redirect(checkout_url)

@app.route("/success/<plan>")
def success(plan):
    plan_data = PRICING.get(plan, {})
    return render_template("success.html", plan=plan_data.get("name", plan), amount=plan_data.get("amount", "0"))

@app.route("/cancel")
def cancel():
    return render_template("cancel.html")

@app.route("/webhook/coinbase", methods=["POST"])
def coinbase_webhook():
    sig = request.headers.get("X-CC-Webhook-Signature", "")
    raw = request.get_data()
    if CB_WEBHOOK_SECRET:
        expected = hmac.new(CB_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return jsonify({"error": "Invalid signature"}), 403
    try:
        body = request.get_json(force=True)
        event = body.get("event", {})
        etype = event.get("type", "")
        data = event.get("data", {})
        if etype == "charge:confirmed":
            meta = data.get("metadata", {})
            pricing = data.get("pricing", {}).get("local", {})
            save_customer({
                "plan":    meta.get("plan", "unknown"),
                "amount":  pricing.get("amount", "0"),
                "currency":pricing.get("currency", "USD"),
                "status":  "confirmed",
                "charge_id": data.get("id", ""),
            })
        elif etype == "charge:failed":
            print(f"Charge failed: {data.get('id','')}")
        elif etype == "charge:pending":
            print(f"Charge pending: {data.get('id','')}")
    except Exception as e:
        print(f"Webhook parse error: {e}")

    return jsonify({"status": "ok"})

# --- Enterprise Sync Webhook (from Enterprise Unified Platform) ---
@app.route("/webhook/enterprise_sync", methods=["POST"])
def receive_enterprise_sync():
    """Receive HMAC-secured revenue events from the Enterprise Unified Platform."""
    signature = request.headers.get("X-Enterprise-Signature", "")
    if not signature:
        return jsonify({"error": "Missing X-Enterprise-Signature header"}), 400

    payload_bytes = request.get_data()
    expected_sig = hmac.new(ENTERPRISE_SYNC_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_sig, signature):
        return jsonify({"error": "Invalid signature"}), 403

    try:
        data = request.get_json(force=True)
        amount = data.get("amount", 0)
        transaction_id = data.get("transaction_id", "")
        customer_id = data.get("customer_id", "")
        plan = data.get("plan", "")

        # Log and process new capital
        print(f"[APEX Sync] Revenue cleared: ${amount} from TX: {transaction_id} (customer: {customer_id}, plan: {plan})")

        # Auto-track as customer if not already
        customers = load_customers()
        exists = any(c.get("charge_id") == transaction_id for c in customers)
        if not exists and amount > 0:
            save_customer({
                "plan": plan or "enterprise-sync",
                "amount": str(amount),
                "currency": "USD",
                "status": "confirmed",
                "charge_id": transaction_id,
                "source": "enterprise_webhook",
            })

        return jsonify({
            "status": "success",
            "capital_allocated": True,
            "mrr_updated": amount,
            "timestamp": str(datetime.datetime.utcnow()),
        })
    except Exception as e:
        print(f"[APEX Sync] Parse error: {e}")
        return jsonify({"error": str(e)}), 500

# --- AI Endpoints ---
@app.route("/genesis", methods=["GET", "POST"])
def genesis():
    if not model:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
    prompt = (request.get_json(force=True, silent=True) or {}).get("prompt",
        "You are GENESIS. Provide 3 specific strategies to reach $5000 MRR in 30 days "
        "for an autonomous AI SaaS platform. Include pricing, acquisition channels, and exact steps.")
    try:
        resp = model.generate_content(prompt)
        return jsonify({"genesis_output": resp.text, "timestamp": str(datetime.datetime.utcnow())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ai/leads")
def ai_leads():
    if not model:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
    try:
        resp = model.generate_content(
            "Generate 5 high-value B2B SaaS lead profiles for an autonomous AI revenue platform. "
            "Include: company, industry, pain point, deal size USD/mo, outreach angle. Return JSON array.")
        return jsonify({"leads": resp.text, "timestamp": str(datetime.datetime.utcnow())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ai/analyze", methods=["POST"])
def ai_analyze():
    if not model:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
    prompt = (request.get_json(force=True, silent=True) or {}).get("prompt", "Analyze this business.")
    try:
        resp = model.generate_content(prompt)
        return jsonify({"analysis": resp.text, "timestamp": str(datetime.datetime.utcnow())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)