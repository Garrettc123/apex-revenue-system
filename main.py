from flask import Flask, jsonify, request, render_template, redirect
import base64
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

# --- Integration Config ---
SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")
SHOPIFY_STORE_DOMAIN   = os.environ.get("SHOPIFY_STORE_DOMAIN", "")
SHOPIFY_ADMIN_TOKEN    = os.environ.get("SHOPIFY_ADMIN_TOKEN", "")

STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

HUBSPOT_ACCESS_TOKEN   = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")

SUPABASE_URL           = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

LINEAR_API_KEY         = os.environ.get("LINEAR_API_KEY", "")
LINEAR_TEAM_ID         = os.environ.get("LINEAR_TEAM_ID", "")

NOTION_TOKEN           = os.environ.get("NOTION_TOKEN", "")
NOTION_PARENT_PAGE_ID  = os.environ.get("NOTION_PARENT_PAGE_ID", "")

DOCUSIGN_ACCESS_TOKEN  = os.environ.get("DOCUSIGN_ACCESS_TOKEN", "")
DOCUSIGN_ACCOUNT_ID    = os.environ.get("DOCUSIGN_ACCOUNT_ID", "")
DOCUSIGN_TEMPLATE_ID   = os.environ.get("DOCUSIGN_TEMPLATE_ID", "")
DOCUSIGN_BASE_URI      = os.environ.get("DOCUSIGN_BASE_URI", "https://na4.docusign.net")

HUNTER_API_KEY         = os.environ.get("HUNTER_API_KEY", "")

PRICING = {
    "starter":    {"amount": "49.00",  "name": "GENESIS Starter",    "label": "$49/mo"},
    "pro":        {"amount": "149.00", "name": "GENESIS Pro",        "label": "$149/mo"},
    "enterprise": {"amount": "499.00", "name": "GENESIS Enterprise", "label": "$499/mo"},
}

# --- Revenue Ledger ---
def load_ledger():
    default = {"events": [], "mrr_target_usd": 5000.0}
    if os.path.exists(REVENUE_LEDGER_FILE):
        try:
            with open(REVENUE_LEDGER_FILE) as f:
                ledger = json.load(f)
                if not isinstance(ledger, dict):
                    return default
                events = ledger.get("events")
                ledger["events"] = events if isinstance(events, list) else []
                try:
                    ledger["mrr_target_usd"] = float(ledger.get("mrr_target_usd", 5000.0))
                except (TypeError, ValueError):
                    ledger["mrr_target_usd"] = 5000.0
                return ledger
        except Exception:
            pass
    return default


def record_revenue_event(event_type, amount_usd, plan, charge_id, source, status, extra=None):
    ledger = load_ledger()
    try:
        parsed_amount = float(amount_usd)
    except (TypeError, ValueError):
        parsed_amount = 0.0
    event = {
        "event_type": event_type,
        "amount_usd": parsed_amount,
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
        "coinbase": "connected" if CB_API_KEY else "set COINBASE_API_KEY",
        "shopify": "connected" if SHOPIFY_ADMIN_TOKEN else "set SHOPIFY_ADMIN_TOKEN",
        "stripe": "connected" if STRIPE_SECRET_KEY else "set STRIPE_SECRET_KEY",
        "hubspot": "connected" if HUBSPOT_ACCESS_TOKEN else "set HUBSPOT_ACCESS_TOKEN",
        "supabase": "connected" if (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY) else "set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY",
        "linear": "connected" if (LINEAR_API_KEY and LINEAR_TEAM_ID) else "set LINEAR_API_KEY + LINEAR_TEAM_ID",
        "notion": "connected" if (NOTION_TOKEN and NOTION_PARENT_PAGE_ID) else "set NOTION_TOKEN + NOTION_PARENT_PAGE_ID",
        "docusign": "connected" if (DOCUSIGN_ACCESS_TOKEN and DOCUSIGN_ACCOUNT_ID) else "set DOCUSIGN_ACCESS_TOKEN + DOCUSIGN_ACCOUNT_ID",
        "hunter": "connected" if HUNTER_API_KEY else "set HUNTER_API_KEY",
    })

@app.route("/metrics")
def metrics():
    ledger = load_ledger()
    events = ledger.get("events", [])
    active = [event for event in events if event.get("status") == "confirmed"]
    mrr = sum(float(event.get("amount_usd", 0) or 0) for event in active)
    mrr_target = float(ledger.get("mrr_target_usd", 5000.0) or 5000.0)
    return jsonify({
        "mrr_usd": mrr,
        "mrr_target_usd": mrr_target,
        "progress_pct": round((mrr / mrr_target) * 100, 2) if mrr_target else 0,
        "active_customers": len(active),
        "total_customers": len(events),
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
            record_revenue_event(
                event_type=etype,
                amount_usd=pricing.get("amount", "0"),
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
    required = {"node_id", "charge_id", "amount_usd", "plan", "source"}
    missing = sorted(required - set(payload.keys()))
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    try:
        amount_usd = float(payload.get("amount_usd"))
    except (TypeError, ValueError):
        return jsonify({"error": "amount_usd must be numeric"}), 400

    record_revenue_event(
        event_type="revenue-collected",
        amount_usd=amount_usd,
        plan=payload.get("plan", "unknown"),
        charge_id=payload.get("charge_id"),
        source="edge",
        status="confirmed",
        extra={
            "node_id": payload.get("node_id"),
            "reported_source": payload.get("source"),
            "reported_timestamp": payload.get("timestamp"),
        },
    )
    return jsonify({"status": "recorded"})


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    etype = payload.get("type", "")
    obj = payload.get("data", {}).get("object", {})
    if etype in ("payment_intent.succeeded", "charge.succeeded"):
        amount_cents = obj.get("amount_received", obj.get("amount", 0))
        try:
            amount_usd = float(amount_cents) / 100.0
        except (TypeError, ValueError):
            amount_usd = 0.0
        metadata = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
        record_revenue_event(
            event_type=etype,
            amount_usd=amount_usd,
            plan=metadata.get("plan", "unknown"),
            charge_id=obj.get("id", ""),
            source="stripe",
            status="confirmed",
            extra={"currency": obj.get("currency", "usd")},
        )
    return jsonify({"status": "ok"})


# --- Integration Helpers ---

def _hubspot_create_deal(order_id, customer_email, customer_name, amount_usd, product_title):
    """Create a deal in HubSpot for a new Shopify order."""
    if not HUBSPOT_ACCESS_TOKEN:
        return None
    payload = {
        "properties": {
            "dealname": f"Shopify Order #{order_id} — {product_title}",
            "amount": str(amount_usd),
            "dealstage": "closedwon",
            "pipeline": "default",
            "closedate": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)),
        }
    }
    try:
        resp = http.post(
            "https://api.hubapi.com/crm/v3/objects/deals",
            headers={
                "Authorization": "Bearer " + HUBSPOT_ACCESS_TOKEN,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"HubSpot error: {e}")
        return None


def _supabase_provision_tenant(order_id, customer_email, customer_name, amount_usd, product_title):
    """Insert a new tenant row in Supabase on customer creation."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    try:
        resp = http.post(
            f"{SUPABASE_URL}/rest/v1/tenants",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": "Bearer " + SUPABASE_SERVICE_ROLE_KEY,
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={
                "shopify_order_id": str(order_id),
                "email": customer_email,
                "name": customer_name,
                "plan": product_title,
                "amount_usd": amount_usd,
                "status": "active",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Supabase error: {e}")
        return None


def _linear_create_project(order_id, customer_name, product_title):
    """Create an onboarding project in Linear for a new customer."""
    if not (LINEAR_API_KEY and LINEAR_TEAM_ID):
        return None
    query = """
    mutation CreateProject($name: String!, $teamIds: [String!]!) {
      projectCreate(input: {name: $name, teamIds: $teamIds, state: "started"}) {
        success
        project { id name }
      }
    }
    """
    try:
        resp = http.post(
            "https://api.linear.app/graphql",
            headers={
                "Authorization": LINEAR_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "variables": {
                    "name": f"Onboarding — {customer_name} ({product_title}) #{order_id}",
                    "teamIds": [LINEAR_TEAM_ID],
                },
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Linear error: {e}")
        return None


def _notion_create_workspace(order_id, customer_name, product_title):
    """Create a client workspace page in Notion."""
    if not (NOTION_TOKEN and NOTION_PARENT_PAGE_ID):
        return None
    try:
        resp = http.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": "Bearer " + NOTION_TOKEN,
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={
                "parent": {"page_id": NOTION_PARENT_PAGE_ID},
                "properties": {
                    "title": {
                        "title": [
                            {"text": {"content": f"{customer_name} — {product_title} #{order_id}"}}
                        ]
                    }
                },
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Notion error: {e}")
        return None


def _docusign_send_contract(order_id, customer_email, customer_name):
    """Send IRAS service agreement via DocuSign envelope from a template."""
    if not (DOCUSIGN_ACCESS_TOKEN and DOCUSIGN_ACCOUNT_ID and DOCUSIGN_TEMPLATE_ID):
        return None
    try:
        resp = http.post(
            f"{DOCUSIGN_BASE_URI}/restapi/v2.1/accounts/{DOCUSIGN_ACCOUNT_ID}/envelopes",
            headers={
                "Authorization": "Bearer " + DOCUSIGN_ACCESS_TOKEN,
                "Content-Type": "application/json",
            },
            json={
                "status": "sent",
                "templateId": DOCUSIGN_TEMPLATE_ID,
                "templateRoles": [
                    {
                        "email": customer_email,
                        "name": customer_name,
                        "roleName": "Client",
                    }
                ],
                "emailSubject": f"IRAS Service Agreement — Order #{order_id}",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"DocuSign error: {e}")
        return None


# --- Shopify Webhook ---

@app.route("/webhook/shopify", methods=["POST"])
def shopify_webhook():
    """Receive Shopify order/paid webhooks and fan-out to downstream integrations."""
    payload_bytes = request.data
    topic = request.headers.get("X-Shopify-Topic", "")

    if SHOPIFY_WEBHOOK_SECRET:
        digest = hmac.new(
            SHOPIFY_WEBHOOK_SECRET.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).digest()
        computed = base64.b64encode(digest).decode()
        received = request.headers.get("X-Shopify-Hmac-Sha256", "")
        if not hmac.compare_digest(computed, received):
            return jsonify({"error": "Invalid signature"}), 400

    try:
        order = json.loads(payload_bytes)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if topic not in ("orders/paid", "orders/create"):
        return jsonify({"status": "ignored", "topic": topic})

    order_id      = order.get("id", "")
    customer      = order.get("customer") or {}
    customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip() or "Unknown"
    customer_email = customer.get("email") or order.get("email", "")
    line_items    = order.get("line_items", [])
    product_title = line_items[0].get("title", "Unknown") if line_items else "Unknown"
    try:
        amount_usd = float(order.get("total_price", 0))
    except (TypeError, ValueError):
        amount_usd = 0.0

    record_revenue_event(
        event_type="shopify.order.paid",
        amount_usd=amount_usd,
        plan=product_title,
        charge_id=str(order_id),
        source="shopify",
        status="confirmed",
        extra={"customer_email": customer_email, "customer_name": customer_name},
    )

    results = {
        "hubspot":  _hubspot_create_deal(order_id, customer_email, customer_name, amount_usd, product_title),
        "supabase": _supabase_provision_tenant(order_id, customer_email, customer_name, amount_usd, product_title),
        "linear":   _linear_create_project(order_id, customer_name, product_title),
        "notion":   _notion_create_workspace(order_id, customer_name, product_title),
        "docusign": _docusign_send_contract(order_id, customer_email, customer_name),
    }

    return jsonify({"status": "processed", "order_id": order_id, "integrations": {
        k: ("ok" if v is not None else "skipped — API key not configured")
        for k, v in results.items()
    }})


# --- Integrations Status ---

@app.route("/integrations/status")
def integrations_status():
    """Return connection status for every configured external integration."""
    return jsonify({
        "shopify":  "connected" if SHOPIFY_ADMIN_TOKEN else "set SHOPIFY_ADMIN_TOKEN",
        "stripe":   "connected" if STRIPE_SECRET_KEY else "set STRIPE_SECRET_KEY",
        "hubspot":  "connected" if HUBSPOT_ACCESS_TOKEN else "set HUBSPOT_ACCESS_TOKEN",
        "supabase": "connected" if (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY) else "set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY",
        "linear":   "connected" if (LINEAR_API_KEY and LINEAR_TEAM_ID) else "set LINEAR_API_KEY + LINEAR_TEAM_ID",
        "notion":   "connected" if (NOTION_TOKEN and NOTION_PARENT_PAGE_ID) else "set NOTION_TOKEN + NOTION_PARENT_PAGE_ID",
        "docusign": "connected" if (DOCUSIGN_ACCESS_TOKEN and DOCUSIGN_ACCOUNT_ID and DOCUSIGN_TEMPLATE_ID) else "set DOCUSIGN_ACCESS_TOKEN + DOCUSIGN_ACCOUNT_ID + DOCUSIGN_TEMPLATE_ID",
        "hunter":   "connected" if HUNTER_API_KEY else "set HUNTER_API_KEY",
        "coinbase": "connected" if CB_API_KEY else "set COINBASE_API_KEY",
        "gemini":   "connected" if GEMINI_API_KEY else "set GEMINI_API_KEY",
    })

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
