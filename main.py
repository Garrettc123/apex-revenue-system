from flask import Flask, jsonify, request, render_template, redirect
import base64
import datetime, os, json, hmac, hashlib, math, tempfile
from pathlib import Path
import fcntl
import requests as http
from google import genai as _genai
from config.tiers import normalize_tier, tier_config, allows_pipeline
from core import lead_enrichment, deal_pipeline, payments

app = Flask(__name__)

# --- Config ---
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
CB_API_KEY         = os.environ.get("COINBASE_API_KEY", "")
CB_WEBHOOK_SECRET  = os.environ.get("COINBASE_WEBHOOK_SECRET", "")
EDGE_WEBHOOK_SECRET = os.environ.get("EDGE_WEBHOOK_SECRET", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
BASE_URL           = os.environ.get("BASE_URL", "https://apex-revenue-system.up.railway.app")
# Durable ledger path (survives redeploys when mounted volume is used).
# Override with REVENUE_LEDGER_FILE for shared storage across replicas.
_DEFAULT_LEDGER = Path(__file__).resolve().parent / "data" / "revenue_ledger.json"
REVENUE_LEDGER_FILE = os.environ.get("REVENUE_LEDGER_FILE", str(_DEFAULT_LEDGER))
REVENUE_DB_FILE = os.environ.get("REVENUE_DB_FILE", str(Path(__file__).resolve().parent / "data" / "revenue_engine.db"))

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
CLEARBIT_API_KEY       = os.environ.get("CLEARBIT_API_KEY", "")
OPENAI_API_KEY         = os.environ.get("OPENAI_API_KEY", "")
STRIPE_PRICE_ID_STARTER = os.environ.get("STRIPE_PRICE_ID_STARTER", "")
STRIPE_PRICE_ID_PRO = os.environ.get("STRIPE_PRICE_ID_PRO", "")
STRIPE_PRICE_ID_ENTERPRISE = os.environ.get("STRIPE_PRICE_ID_ENTERPRISE", "")

payments.init_db(REVENUE_DB_FILE)

PRICING = {
    "starter":    {"amount": "49.00",  "name": "GENESIS Starter",    "label": "$49/mo"},
    "pro":        {"amount": "149.00", "name": "GENESIS Pro",        "label": "$149/mo"},
    "enterprise": {"amount": "499.00", "name": "GENESIS Enterprise", "label": "$499/mo"},
}

# --- Revenue Ledger ---
def _ensure_ledger_dir():
    Path(REVENUE_LEDGER_FILE).parent.mkdir(parents=True, exist_ok=True)


def load_ledger():
    default = {"events": [], "mrr_target_usd": 5000.0, "seen_charge_ids": []}
    if os.path.exists(REVENUE_LEDGER_FILE):
        try:
            with open(REVENUE_LEDGER_FILE) as f:
                ledger = json.load(f)
                if not isinstance(ledger, dict):
                    return default
                events = ledger.get("events")
                ledger["events"] = events if isinstance(events, list) else []
                seen = ledger.get("seen_charge_ids")
                ledger["seen_charge_ids"] = seen if isinstance(seen, list) else []
                # Preserve valid zero; only default when missing/None
                raw_target = ledger.get("mrr_target_usd")
                if raw_target is None:
                    ledger["mrr_target_usd"] = 5000.0
                else:
                    try:
                        ledger["mrr_target_usd"] = float(raw_target)
                    except (TypeError, ValueError):
                        ledger["mrr_target_usd"] = 5000.0
                return ledger
        except Exception:
            pass
    return default


def _atomic_write_ledger(ledger):
    """Write ledger with exclusive lock and atomic replace."""
    _ensure_ledger_dir()
    path = Path(REVENUE_LEDGER_FILE)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "a+") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=".ledger-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w") as tmp:
                    json.dump(ledger, tmp, indent=2)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_name, str(path))
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def record_revenue_event(event_type, amount_usd, plan, charge_id, source, status, extra=None):
    """Append a confirmed event once per charge_id (idempotent on retries)."""
    try:
        parsed_amount = float(amount_usd)
    except (TypeError, ValueError):
        raise ValueError("amount_usd must be numeric")
    if not math.isfinite(parsed_amount):
        raise ValueError("amount_usd must be finite")

    charge_key = str(charge_id or "").strip()
    _ensure_ledger_dir()
    path = Path(REVENUE_LEDGER_FILE)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with open(lock_path, "a+") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            ledger = load_ledger()
            seen = set(ledger.get("seen_charge_ids") or [])
            if charge_key and charge_key in seen:
                return {"duplicate": True, "charge_id": charge_key}

            event = {
                "event_type": event_type,
                "amount_usd": parsed_amount,
                "plan": plan,
                "charge_id": charge_key,
                "source": source,
                "status": status,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if extra:
                # Only merge keys with non-None values so omitted timestamp
                # does not overwrite the server-generated UTC timestamp.
                event.update({k: v for k, v in extra.items() if v is not None})

            ledger["events"].append(event)
            if charge_key:
                seen.add(charge_key)
                ledger["seen_charge_ids"] = list(seen)

            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=".ledger-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w") as tmp:
                    json.dump(ledger, tmp, indent=2)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_name, str(path))
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            return {"duplicate": False, "charge_id": charge_key}
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

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
        "shopify": "connected" if SHOPIFY_WEBHOOK_SECRET else "set SHOPIFY_WEBHOOK_SECRET",
        "stripe": "connected" if STRIPE_SECRET_KEY else "set STRIPE_SECRET_KEY",
        "hubspot": "connected" if HUBSPOT_ACCESS_TOKEN else "set HUBSPOT_ACCESS_TOKEN",
        "supabase": "connected" if (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY) else "set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY",
        "linear": "connected" if (LINEAR_API_KEY and LINEAR_TEAM_ID) else "set LINEAR_API_KEY + LINEAR_TEAM_ID",
        "notion": "connected" if (NOTION_TOKEN and NOTION_PARENT_PAGE_ID) else "set NOTION_TOKEN + NOTION_PARENT_PAGE_ID",
        "docusign": "connected" if (DOCUSIGN_ACCESS_TOKEN and DOCUSIGN_ACCOUNT_ID and DOCUSIGN_TEMPLATE_ID) else "set DOCUSIGN_ACCESS_TOKEN + DOCUSIGN_ACCOUNT_ID + DOCUSIGN_TEMPLATE_ID",
        "hunter": "connected" if HUNTER_API_KEY else "set HUNTER_API_KEY",
        "clearbit": "connected" if CLEARBIT_API_KEY else "set CLEARBIT_API_KEY",
        "openai_fallback": "connected" if OPENAI_API_KEY else "set OPENAI_API_KEY (optional fallback)",
        "payments": "ready",
        "lead_enrichment": "ready",
        "deal_pipeline": "ready",
    })

@app.route("/metrics")
def metrics():
    ledger = load_ledger()
    events = ledger.get("events", [])
    active = [event for event in events if event.get("status") == "confirmed"]
    mrr = sum(float(event.get("amount_usd", 0) or 0) for event in active)
    # Preserve valid zero target; only default when missing
    raw_target = ledger.get("mrr_target_usd")
    if raw_target is None:
        mrr_target = 5000.0
    else:
        try:
            mrr_target = float(raw_target)
        except (TypeError, ValueError):
            mrr_target = 5000.0
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
    # Fail closed: require webhook secret in production paths
    if not CB_WEBHOOK_SECRET:
        return jsonify({"error": "COINBASE_WEBHOOK_SECRET not configured"}), 503

    payload = request.data
    sig = request.headers.get("X-CC-Webhook-Signature", "")
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
                record_revenue_event(
                    event_type=etype,
                    amount_usd=pricing.get("amount", "0"),
                    plan=meta.get("plan", "unknown"),
                    charge_id=data.get("id", ""),
                    source="coinbase",
                    status="confirmed",
                    extra={"currency": pricing.get("currency", "USD")},
                )
            except ValueError as ve:
                return jsonify({"error": str(ve)}), 400
        elif etype == "charge:failed":
            print(f"Charge failed: {data.get('id','')}")
        elif etype == "charge:pending":
            print(f"Charge pending: {data.get('id','')}")
    except Exception as e:
        print(f"Webhook parse error: {e}")

    return jsonify({"status": "ok"})


def _verify_edge_hmac(raw_body: bytes) -> bool:
    """Verify X-Edge-Signature header (hex HMAC-SHA256 of raw body)."""
    sig = request.headers.get("X-Edge-Signature", "")
    if not sig or not EDGE_WEBHOOK_SECRET:
        return False
    computed = hmac.new(
        EDGE_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, sig)


@app.route("/webhook/edge/revenue", methods=["POST"])
def edge_revenue_webhook():
    if not EDGE_WEBHOOK_SECRET:
        return jsonify({"error": "EDGE_WEBHOOK_SECRET not configured"}), 503

    raw = request.data
    if not _verify_edge_hmac(raw):
        return jsonify({"error": "Invalid edge signature"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    required = {"node_id", "charge_id", "amount_usd", "plan", "source"}
    missing = sorted(required - set(payload.keys()))
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    try:
        amount_usd = float(payload.get("amount_usd"))
    except (TypeError, ValueError):
        return jsonify({"error": "amount_usd must be numeric"}), 400
    if not math.isfinite(amount_usd):
        return jsonify({"error": "amount_usd must be finite"}), 400

    extra = {
        "node_id": payload.get("node_id"),
        "reported_source": payload.get("source"),
    }
    # Only pass client timestamp when present (preserve server UTC otherwise)
    if payload.get("timestamp") is not None:
        extra["reported_timestamp"] = payload.get("timestamp")

    try:
        result = record_revenue_event(
            event_type="revenue-collected",
            amount_usd=amount_usd,
            plan=payload.get("plan", "unknown"),
            charge_id=payload.get("charge_id"),
            source="edge",
            status="confirmed",
            extra=extra,
        )
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

    if result.get("duplicate"):
        return jsonify({"status": "duplicate", "charge_id": result.get("charge_id")})
    return jsonify({"status": "recorded"})


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "STRIPE_WEBHOOK_SECRET not configured"}), 503

    # Verify signature against raw body before parsing JSON
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        import stripe
        stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ImportError:
        # Minimal HMAC check when stripe package unavailable in env
        # Stripe uses a timestamped scheme; without the SDK we reject.
        return jsonify({"error": "stripe package required for signature verification"}), 503
    except Exception:
        return jsonify({"error": "Invalid Stripe signature"}), 400

    event = request.get_json(force=True, silent=True) or {}
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    if etype in ("payment_intent.succeeded", "charge.succeeded"):
        amount_cents = obj.get("amount_received", obj.get("amount", 0))
        try:
            amount_usd = float(amount_cents) / 100.0
        except (TypeError, ValueError):
            return jsonify({"error": "invalid amount"}), 400
        if not math.isfinite(amount_usd):
            return jsonify({"error": "amount must be finite"}), 400
        metadata = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
        try:
            record_revenue_event(
                event_type=etype,
                amount_usd=amount_usd,
                plan=metadata.get("plan", "unknown"),
                charge_id=obj.get("id", ""),
                source="stripe",
                status="confirmed",
                extra={"currency": obj.get("currency", "usd")},
            )
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
    return jsonify({"status": "ok"})


# --- Integration Helpers ---

def _hubspot_create_deal(order_id, customer_email, customer_name, amount_usd, product_title):
    """Create a deal in HubSpot for a new Shopify order."""
    if not HUBSPOT_ACCESS_TOKEN:
        return "skipped"
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
        return "skipped"
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
        return "skipped"
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
        return "skipped"
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
        return "skipped"
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

    if not SHOPIFY_WEBHOOK_SECRET and not app.config.get("TESTING"):
        return jsonify({"error": "SHOPIFY_WEBHOOK_SECRET not configured"}), 503

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

    if topic == "orders/paid":
        try:
            record_revenue_event(
                event_type="shopify.order.paid",
                amount_usd=amount_usd,
                plan=product_title,
                charge_id=str(order_id),
                source="shopify",
                status="confirmed",
                extra={"customer_email": customer_email, "customer_name": customer_name},
            )
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400

    results = {
        "hubspot":  _hubspot_create_deal(order_id, customer_email, customer_name, amount_usd, product_title),
        "supabase": _supabase_provision_tenant(order_id, customer_email, customer_name, amount_usd, product_title),
        "linear":   _linear_create_project(order_id, customer_name, product_title),
        "notion":   _notion_create_workspace(order_id, customer_name, product_title),
        "docusign": _docusign_send_contract(order_id, customer_email, customer_name),
    }

    def _result_label(v):
        if v == "skipped":
            return "skipped — API key not configured"
        if v is None:
            return "error"
        return "ok"

    return jsonify({"status": "processed", "order_id": order_id, "integrations": {
        k: _result_label(v) for k, v in results.items()
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
        "clearbit": "connected" if CLEARBIT_API_KEY else "set CLEARBIT_API_KEY",
        "openai_fallback": "connected" if OPENAI_API_KEY else "set OPENAI_API_KEY",
        "coinbase": "connected" if CB_API_KEY else "set COINBASE_API_KEY",
        "gemini":   "connected" if GEMINI_API_KEY else "set GEMINI_API_KEY",
    })


def _required_customer_id(payload: dict):
    customer_id = str((payload or {}).get("customer_id") or "").strip()
    if not customer_id:
        return None, (jsonify({"error": "customer_id is required"}), 400)
    return customer_id, None


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    payload = request.get_json(force=True, silent=True) or {}
    customer_id, err = _required_customer_id(payload)
    if err:
        return err
    tier = normalize_tier(payload.get("tier"))
    try:
        session = payments.create_checkout_session(
            customer_id=customer_id,
            tier=tier,
            base_url=BASE_URL,
            price_ids={
                "starter": STRIPE_PRICE_ID_STARTER,
                "pro": STRIPE_PRICE_ID_PRO,
                "enterprise": STRIPE_PRICE_ID_ENTERPRISE,
            },
            stripe_secret_key=STRIPE_SECRET_KEY,
        )
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except RuntimeError as re:
        return jsonify({"error": str(re)}), 503
    return jsonify({"status": "created", "checkout": session})


@app.route("/api/stripe/webhook", methods=["POST"])
def api_stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "STRIPE_WEBHOOK_SECRET not configured"}), 503
    try:
        event = payments.verify_webhook_signature(
            payload=request.data,
            sig_header=request.headers.get("Stripe-Signature", ""),
            webhook_secret=STRIPE_WEBHOOK_SECRET,
        )
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception:
        return jsonify({"error": "Invalid Stripe signature"}), 400
    payments.handle_webhook_event(REVENUE_DB_FILE, event)
    return jsonify({"status": "ok"})


@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    payload = request.get_json(force=True, silent=True) or {}
    customer_id, err = _required_customer_id(payload)
    if err:
        return err
    lead = payload.get("lead") if isinstance(payload.get("lead"), dict) else payload
    payments.provision_customer(REVENUE_DB_FILE, customer_id=customer_id)
    allowed, reason, customer = payments.check_enrichment_access(REVENUE_DB_FILE, customer_id)
    if not allowed:
        return jsonify({"error": reason, "tier": customer.get("tier")}), 402

    enrichment = lead_enrichment.enrich_lead(lead, hunter_api_key=HUNTER_API_KEY, clearbit_api_key=CLEARBIT_API_KEY)
    enrichment_id = payments.record_enrichment(REVENUE_DB_FILE, customer_id, lead, enrichment)
    pipeline_entry = deal_pipeline.add_prospect(
        REVENUE_DB_FILE,
        customer_id=customer_id,
        prospect={
            "name": lead.get("full_name") or lead.get("name"),
            "email": lead.get("email"),
            "company": lead.get("company_name") or lead.get("company"),
            "company_size": lead.get("company_size"),
            "budget_usd": lead.get("budget_usd"),
            "decision_timeline_days": lead.get("decision_timeline_days"),
        },
        enrichment=enrichment,
    )
    return jsonify(
        {
            "enrichment_id": enrichment_id,
            "customer_id": customer_id,
            "tier": customer.get("tier"),
            "result": enrichment,
            "pipeline_entry_id": pipeline_entry["id"],
        }
    )


@app.route("/api/enrich/bulk", methods=["POST"])
def api_enrich_bulk():
    payload = request.get_json(force=True, silent=True) or {}
    customer_id, err = _required_customer_id(payload)
    if err:
        return err
    leads = payload.get("leads")
    if not isinstance(leads, list) or not leads:
        return jsonify({"error": "leads must be a non-empty list"}), 400

    payments.provision_customer(REVENUE_DB_FILE, customer_id=customer_id)
    customer = payments.get_customer(REVENUE_DB_FILE, customer_id)
    config = tier_config(customer.get("tier"))
    if not config.get("bulk_enrichment"):
        return jsonify({"error": "Bulk enrichment requires a paid subscription tier"}), 403
    batch_limit = int(config.get("bulk_max_batch") or 0)
    if batch_limit and len(leads) > batch_limit:
        return jsonify({"error": f"Batch exceeds tier limit ({batch_limit})"}), 400

    saved = []
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        allowed, reason, _ = payments.check_enrichment_access(REVENUE_DB_FILE, customer_id)
        if not allowed:
            return jsonify({"error": reason}), 402
        enrichment = lead_enrichment.enrich_lead(lead, hunter_api_key=HUNTER_API_KEY, clearbit_api_key=CLEARBIT_API_KEY)
        enrichment_id = payments.record_enrichment(REVENUE_DB_FILE, customer_id, lead, enrichment)
        deal_pipeline.add_prospect(
            REVENUE_DB_FILE,
            customer_id=customer_id,
            prospect={
                "name": lead.get("full_name") or lead.get("name"),
                "email": lead.get("email"),
                "company": lead.get("company_name") or lead.get("company"),
                "company_size": lead.get("company_size"),
                "budget_usd": lead.get("budget_usd"),
                "decision_timeline_days": lead.get("decision_timeline_days"),
            },
            enrichment=enrichment,
        )
        saved.append({"enrichment_id": enrichment_id, "result": enrichment})
    return jsonify({"status": "ok", "count": len(saved), "results": saved})


@app.route("/api/pipeline/prospect", methods=["POST"])
def api_pipeline_prospect():
    payload = request.get_json(force=True, silent=True) or {}
    customer_id, err = _required_customer_id(payload)
    if err:
        return err
    customer = payments.get_customer(REVENUE_DB_FILE, customer_id)
    if not allows_pipeline(customer.get("tier")):
        return jsonify({"error": "Pipeline access requires Starter tier or higher"}), 403
    prospect = payload.get("prospect")
    if not isinstance(prospect, dict):
        return jsonify({"error": "prospect object is required"}), 400
    lead = deal_pipeline.add_prospect(REVENUE_DB_FILE, customer_id=customer_id, prospect=prospect, enrichment=payload.get("enrichment"))
    return jsonify({"status": "created", "lead": lead}), 201


@app.route("/api/pipeline/leads")
def api_pipeline_leads():
    customer_id = request.args.get("customer_id", "").strip()
    if not customer_id:
        return jsonify({"error": "customer_id is required"}), 400
    customer = payments.get_customer(REVENUE_DB_FILE, customer_id)
    if not allows_pipeline(customer.get("tier")):
        return jsonify({"error": "Pipeline access requires Starter tier or higher"}), 403
    return jsonify({"leads": deal_pipeline.list_leads(REVENUE_DB_FILE, customer_id)})


@app.route("/api/pipeline/stats")
def api_pipeline_stats():
    customer_id = request.args.get("customer_id", "").strip()
    if customer_id:
        customer = payments.get_customer(REVENUE_DB_FILE, customer_id)
        if not allows_pipeline(customer.get("tier")):
            return jsonify({"error": "Pipeline access requires Starter tier or higher"}), 403
    stats = deal_pipeline.pipeline_stats(REVENUE_DB_FILE, customer_id=customer_id or None)
    return jsonify(stats)


@app.route("/api/dashboard/metrics")
def dashboard_metrics():
    base_metrics = metrics().get_json()
    pipeline = deal_pipeline.pipeline_stats(REVENUE_DB_FILE, customer_id=None)
    return jsonify(
        {
            "mrr_usd": base_metrics.get("mrr_usd", 0),
            "leads_enriched_today": payments.enrichments_today_count(REVENUE_DB_FILE),
            "pipeline_conversion_rate_pct": pipeline.get("conversion_rate_pct", 0),
            "stripe_customer_count": payments.stripe_customer_count(REVENUE_DB_FILE),
        }
    )


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

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
