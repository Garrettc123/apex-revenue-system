from flask import Flask, jsonify, request, render_template, redirect
import datetime, os, json
import google.generativeai as genai
import stripe

app = Flask(__name__)

# --- Config ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "https://apex-revenue-system.up.railway.app")
CUSTOMERS_FILE = "/tmp/customers.json"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

PRICING = {
    "starter":    {"amount": 4900,  "name": "GENESIS Starter",    "label": "$49/mo"},
    "pro":        {"amount": 14900, "name": "GENESIS Pro",        "label": "$149/mo"},
    "enterprise": {"amount": 49900, "name": "GENESIS Enterprise", "label": "$499/mo"},
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
        "stripe": "connected" if STRIPE_SECRET_KEY else "set STRIPE_SECRET_KEY"
    })

@app.route("/metrics")
def metrics():
    customers = load_customers()
    active = [c for c in customers if c.get("status") == "active"]
    mrr = sum(c.get("amount", 0) for c in active) / 100
    return jsonify({
        "mrr_usd": mrr,
        "mrr_target_usd": 5000,
        "progress_pct": round((mrr / 5000) * 100, 2),
        "active_customers": len(active),
        "total_customers": len(customers),
        "gap_to_target": max(0, 5000 - mrr),
    })

# --- Stripe Checkout ---
@app.route("/checkout/<plan>")
def checkout(plan):
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe not configured. Set STRIPE_SECRET_KEY in Railway Variables."}), 503
    if plan not in PRICING:
        return redirect("/"), 302
    p = PRICING[plan]
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": p["amount"],
                    "recurring": {"interval": "month"},
                    "product_data": {"name": p["name"], "description": "Apex Revenue System — Autonomous AI Platform"},
                },
                "quantity": 1,
            }],
            metadata={"plan": plan},
            success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/?cancelled=1",
        )
        return redirect(session.url, code=303)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/success")
def success():
    session_id = request.args.get("session_id", "")
    email = "your inbox"
    if session_id and STRIPE_SECRET_KEY:
        try:
            sess = stripe.checkout.Session.retrieve(session_id)
            email = sess.customer_details.email if sess.customer_details else "your inbox"
            save_customer({
                "email": email,
                "plan": sess.metadata.get("plan", "unknown"),
                "amount": sess.amount_total,
                "status": "active",
                "stripe_session": session_id,
            })
        except Exception:
            pass
    return render_template("success.html", email=email)

# --- Stripe Webhook ---
@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature", "")
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"status": "no webhook secret"}), 200
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Bad signature"}), 400
    etype = event["type"]
    if etype == "checkout.session.completed":
        s = event["data"]["object"]
        save_customer({
            "email": (s.get("customer_details") or {}).get("email", "unknown"),
            "amount": s.get("amount_total", 0),
            "status": "active",
            "stripe_session": s.get("id", ""),
            "plan": (s.get("metadata") or {}).get("plan", "unknown"),
        })
    elif etype == "customer.subscription.deleted":
        pass  # handle churn tracking here
    return jsonify({"status": "ok"})

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
