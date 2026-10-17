from flask import Flask, jsonify, request, render_template, redirect
import base64
import datetime, os, json, hmac, hashlib, math, tempfile
from pathlib import Path
import fcntl
import requests as http
from google import genai as _genai

# GAR-530: every outbound charge/send must pass the approval gate (fails closed).
from approval_gate import ApprovalRequired, require_approval, require_standing_approval

# Hardening audit Oct 2026: internal/AI endpoints need APEX_API_KEY (fail closed, rate limited).
from api_auth import require_api_key

app = Flask(__name__)

# APEX sovereign expert agent
from apex_agent.apex_bootstrap import register_apex
register_apex(app)

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
    "starter":    {"amount": "49.00",  "name": "GENESIS Starter",    "label": "$49 one-time"},
    "pro":        {"amount": "149.00", "name": "GENESIS Pro",        "label": "$149 one-time"},
    "enterprise": {"amount": "499.00", "name": "GENESIS Enterprise", "label": "$499 one-time"},
}

# --- Revenue Ledger ---
def _ensure_ledger_dir():
    Path(REVENUE_LEDGER_FILE).parent.mkdir(parents=True, exist_ok=True)


def load_ledger():
    default = {"events": [], "mrr_target_usd": 5000.0, "seen_charge_ids": []}
    if not os.path.exists(REVENUE_LEDGER_FILE):
        return default

    # A corrupt or unreadable ledger must never be silently treated as empty:
    # doing so makes the next successful write overwrite revenue history.
    try:
        with open(REVENUE_LEDGER_FILE, "r", encoding="utf-8") as ledger_file:
            ledger = json.load(ledger_file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Revenue ledger is unreadable; refusing to replace it: {REVENUE_LEDGER_FILE}"
        ) from exc

    if not isinstance(ledger, dict):
        raise RuntimeError("Revenue ledger schema is invalid: root must be an object")
    events = ledger.get("events", [])
    seen = ledger.get("seen_charge_ids", [])
    if not isinstance(events, list) or not isinstance(seen, list):
        raise RuntimeError("Revenue ledger schema is invalid: events/seen_charge_ids must be arrays")
    ledger["events"] = events
    ledger["seen_charge_ids"] = seen

    # Preserve a valid zero target; default only when absent or malformed.
    raw_target = ledger.get("mrr_target_usd")
    if raw_target is None:
        ledger["mrr_target_usd"] = 5000.0
    else:
        try:
            target = float(raw_target)
            ledger["mrr_target_usd"] = target if math.isfinite(target) and target >= 0 else 5000.0
        except (TypeError, ValueError):
            ledger["mrr_target_usd"] = 5000.0
    return ledger


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


def record_revenue_event(
    event_type, amount_usd, plan, charge_id, source, status, extra=None,
    revenue_type="one_time", monthly_equivalent_usd=None,
):
    """Append a confirmed transaction once per provider event ID.

    Revenue and MRR are kept separate. Historical events without an explicit
    recurring classification are treated as one-time revenue, never inferred
    to be subscriptions.
    """
    try:
        parsed_amount = float(amount_usd)
    except (TypeError, ValueError):
        raise ValueError("amount_usd must be numeric")
    if not math.isfinite(parsed_amount):
        raise ValueError("amount_usd must be finite")
    if revenue_type not in {"one_time", "recurring"}:
        raise ValueError("revenue_type must be one_time or recurring")

    monthly_equivalent = None
    if monthly_equivalent_usd is not None:
        try:
            monthly_equivalent = float(monthly_equivalent_usd)
        except (TypeError, ValueError):
            raise ValueError("monthly_equivalent_usd must be numeric")
        if not math.isfinite(monthly_equivalent) or monthly_equivalent < 0:
            raise ValueError("monthly_equivalent_usd must be finite and non-negative")
    if revenue_type == "one_time" and monthly_equivalent is not None:
        raise ValueError("one-time revenue cannot have monthly_equivalent_usd")

    charge_key = str(charge_id or "").strip()
    if not charge_key:
        raise ValueError("charge_id is required for idempotent revenue recording")
    _ensure_ledger_dir()
    path = Path(REVENUE_LEDGER_FILE)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with open(lock_path, "a+") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            ledger = load_ledger()
            seen = set(ledger.get("seen_charge_ids") or [])
            if charge_key in seen:
                return {"duplicate": True, "charge_id": charge_key}

            event = {
                "event_type": event_type,
                "amount_usd": parsed_amount,
                "plan": plan,
                "charge_id": charge_key,
                "source": source,
                "status": status,
                "revenue_type": revenue_type,
                "monthly_equivalent_usd": monthly_equivalent,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if extra:
                # Never let auxiliary metadata overwrite transaction fields.
                reserved = set(event)
                event.update({
                    k: v for k, v in extra.items()
                    if v is not None and k not in reserved
                })

            ledger["events"].append(event)
            seen.add(charge_key)
            ledger["seen_charge_ids"] = sorted(seen)

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
    })

@app.route("/metrics")
@require_api_key
def metrics():
    ledger = load_ledger()
    events = ledger.get("events", [])
    confirmed = [event for event in events if event.get("status") == "confirmed"]

    # Only explicitly classified recurring events contribute to MRR. Older ledger
    # entries lacking revenue_type stay classified as one-time to avoid inventing
    # subscription revenue.
    mrr = sum(
        float(event.get("monthly_equivalent_usd") or 0)
        for event in confirmed
        if event.get("revenue_type") == "recurring"
    )
    total_revenue = sum(float(event.get("amount_usd", 0) or 0) for event in confirmed)
    one_time_revenue = sum(
        float(event.get("amount_usd", 0) or 0)
        for event in confirmed
        if event.get("revenue_type", "one_time") != "recurring"
    )
    subscription_invoice_revenue = sum(
        float(event.get("amount_usd", 0) or 0)
        for event in confirmed
        if event.get("revenue_type") == "recurring"
    )

    def customer_key(event):
        for field in ("customer_id", "customer_email", "email", "tenant_id"):
            value = str(event.get(field) or "").strip().lower()
            if value:
                return f"{field}:{value}"
        return None

    all_customers = {key for event in events if (key := customer_key(event))}
    confirmed_customers = {key for event in confirmed if (key := customer_key(event))}

    raw_target = ledger.get("mrr_target_usd")
    if raw_target is None:
        mrr_target = 5000.0
    else:
        try:
            mrr_target = float(raw_target)
        except (TypeError, ValueError):
            mrr_target = 5000.0

    return jsonify({
        "mrr_usd": round(mrr, 2),
        "mrr_target_usd": mrr_target,
        "progress_pct": round((mrr / mrr_target) * 100, 2) if mrr_target else 0,
        "active_customers": len(confirmed_customers),
        "total_customers": len(all_customers),
        "confirmed_transactions": len(confirmed),
        "total_transactions": len(events),
        "confirmed_revenue_usd": round(total_revenue, 2),
        "one_time_revenue_usd": round(one_time_revenue, 2),
        "subscription_invoice_revenue_usd": round(subscription_invoice_revenue, 2),
        "gap_to_target": round(max(0, mrr_target - mrr), 2),
    })

# --- Coinbase Commerce Checkout ---
@app.route("/checkout/<plan>")
def checkout(plan):
    if not CB_API_KEY:
        return jsonify({"error": "Coinbase not configured. Set COINBASE_API_KEY in Railway Variables."}), 503
    if plan not in PRICING:
        return redirect("/")
    p = PRICING[plan]
    # GAR-530 (A1): buyer-initiated checkout needs Garrett's standing approval for this plan.
    try:
        require_standing_approval(
            "charge.checkout",
            plan=f"coinbase/{plan}",
            amount_cents=int(round(float(p["amount"]) * 100)),
            currency="usd",
            site="apex.main.checkout",
        )
    except ApprovalRequired as exc:
        return jsonify({"error": "Checkout for this plan is not approved yet.",
                        "reason": exc.reason}), 403
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
    if not CB_WEBHOOK_SECRET:
        return jsonify({"error": "COINBASE_WEBHOOK_SECRET not configured"}), 503

    payload = request.get_data()
    signature = request.headers.get("X-CC-Webhook-Signature", "")
    computed = hmac.new(
        CB_WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    if not signature or not hmac.compare_digest(computed, signature):
        return jsonify({"error": "Invalid signature"}), 400

    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({"error": "Invalid JSON"}), 400
    if not isinstance(envelope, dict) or not isinstance(envelope.get("event"), dict):
        return jsonify({"error": "Invalid Coinbase webhook envelope"}), 400

    event = envelope["event"]
    event_type = str(event.get("type") or "")
    data = event.get("data")
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid Coinbase event data"}), 400

    if event_type == "charge:confirmed":
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        pricing = data.get("pricing") if isinstance(data.get("pricing"), dict) else {}
        local_price = pricing.get("local") if isinstance(pricing.get("local"), dict) else {}
        charge_id = str(data.get("id") or "").strip()
        if not charge_id:
            return jsonify({"error": "charge id is required"}), 400
        try:
            amount_usd = float(local_price.get("amount"))
        except (TypeError, ValueError):
            return jsonify({"error": "confirmed charge amount is invalid"}), 400
        if not math.isfinite(amount_usd) or amount_usd <= 0:
            return jsonify({"error": "confirmed charge amount must be finite and positive"}), 400
        try:
            result = record_revenue_event(
                event_type=event_type,
                amount_usd=amount_usd,
                plan=metadata.get("plan", "unknown"),
                charge_id=charge_id,
                source="coinbase",
                status="confirmed",
                extra={
                    "currency": local_price.get("currency", "USD"),
                    "customer_id": metadata.get("customer_id"),
                    "customer_email": metadata.get("customer_email"),
                },
                revenue_type="one_time",
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({
            "status": "duplicate" if result.get("duplicate") else "recorded",
            "charge_id": charge_id,
        })

    if event_type == "charge:failed":
        print(f"Charge failed: {data.get('id', '')}")
        return jsonify({"status": "accepted", "event_type": event_type})
    if event_type == "charge:pending":
        print(f"Charge pending: {data.get('id', '')}")
        return jsonify({"status": "accepted", "event_type": event_type})

    # Validly signed but unsupported event types do not mutate revenue.
    return jsonify({"status": "ignored", "event_type": event_type})


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
        "customer_id": payload.get("customer_id"),
        "customer_email": payload.get("customer_email"),
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

    try:
        import stripe
    except ImportError:
        return jsonify({"error": "stripe package required for signature verification"}), 503

    try:
        verified = stripe.Webhook.construct_event(
            request.data, request.headers.get("Stripe-Signature", ""), STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        return jsonify({"error": "Invalid Stripe signature or webhook payload"}), 400

    event = verified if isinstance(verified, dict) else verified.to_dict_recursive()
    if not isinstance(event, dict):
        return jsonify({"error": "Invalid Stripe webhook payload"}), 400
    etype = str(event.get("type") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    obj = data.get("object") if isinstance(data.get("object"), dict) else {}

    try:
        if etype == "invoice.paid":
            # invoice.paid is the canonical recurring-payment event. Ignore the
            # related PaymentIntent / Charge webhooks for the same invoice.
            subscription_id = obj.get("subscription")
            amount_cents = obj.get("amount_paid", 0)
            amount_usd = float(amount_cents) / 100.0
            if not math.isfinite(amount_usd) or amount_usd < 0:
                return jsonify({"error": "invoice amount_paid must be finite and non-negative"}), 400

            monthly_equivalent = None
            lines = obj.get("lines") if isinstance(obj.get("lines"), dict) else {}
            line_items = lines.get("data") if isinstance(lines.get("data"), list) else []
            for line in line_items:
                if not isinstance(line, dict):
                    continue
                price = line.get("price") if isinstance(line.get("price"), dict) else {}
                recurring = price.get("recurring") if isinstance(price.get("recurring"), dict) else {}
                interval = recurring.get("interval")
                try:
                    count = max(1, int(recurring.get("interval_count") or 1))
                except (TypeError, ValueError):
                    count = 1
                if interval == "month":
                    monthly_equivalent = amount_usd / count
                elif interval == "year":
                    monthly_equivalent = amount_usd / (12 * count)
                elif interval == "week":
                    monthly_equivalent = amount_usd * 52 / (12 * count)
                elif interval == "day":
                    monthly_equivalent = amount_usd * 365 / (12 * count)
                if monthly_equivalent is not None:
                    break

            customer = obj.get("customer")
            if isinstance(customer, dict):
                customer = customer.get("id")
            try:
                record_revenue_event(
                    event_type=etype,
                    amount_usd=amount_usd,
                    plan="stripe_subscription",
                    charge_id=str(obj.get("id") or ""),
                    source="stripe",
                    status="confirmed",
                    extra={
                        "currency": obj.get("currency", "usd"),
                        "customer_id": customer,
                        "subscription_id": subscription_id.get("id") if isinstance(subscription_id, dict) else subscription_id,
                    },
                    revenue_type="recurring",
                    monthly_equivalent_usd=monthly_equivalent,
                )
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        elif etype == "payment_intent.succeeded":
            # Subscription invoice PaymentIntents are accounted for by invoice.paid.
            # Counting both events inflates revenue and MRR, so only count standalone
            # PaymentIntents here.
            if obj.get("invoice"):
                return jsonify({"status": "ignored", "reason": "invoice payment is recorded by invoice.paid"})

            amount_cents = obj.get("amount_received", obj.get("amount", 0))
            try:
                amount_usd = float(amount_cents) / 100.0
            except (TypeError, ValueError):
                return jsonify({"error": "invalid amount"}), 400
            if not math.isfinite(amount_usd) or amount_usd < 0:
                return jsonify({"error": "amount must be finite and non-negative"}), 400
            metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
            customer = obj.get("customer")
            if isinstance(customer, dict):
                customer = customer.get("id")
            try:
                result = record_revenue_event(
                    event_type=etype,
                    amount_usd=amount_usd,
                    plan=metadata.get("plan", "one_time"),
                    charge_id=str(obj.get("id") or ""),
                    source="stripe",
                    status="confirmed",
                    extra={
                        "currency": obj.get("currency", "usd"),
                        "customer_id": customer or metadata.get("customer_id"),
                        "customer_email": metadata.get("customer_email"),
                    },
                    revenue_type="one_time",
                )
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify({"status": "duplicate" if result.get("duplicate") else "recorded"})

        # Charge.success is intentionally not counted: Stripe also sends the
        # corresponding PaymentIntent event, so counting both double-counts it.
        return jsonify({"status": "ok"})
    except (TypeError, ValueError):
        return jsonify({"error": "invalid Stripe revenue payload"}), 400


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


def _docusign_send_contract(order_id, customer_email, customer_name, approval_id=None):
    """Send IRAS service agreement via DocuSign envelope from a template.

    GAR-530 (A4): needs a per-action ``send.contract`` approval scoped to the signer's
    email. Without one nothing is sent and "refused" is returned.
    """
    if not (DOCUSIGN_ACCESS_TOKEN and DOCUSIGN_ACCOUNT_ID and DOCUSIGN_TEMPLATE_ID):
        return "skipped"
    try:
        require_approval("send.contract", approval_id, to=customer_email,
                         site="apex.main._docusign_send_contract",
                         correlation_id=str(order_id) if order_id else None)
    except ApprovalRequired as exc:
        print(f"DocuSign send refused by approval gate: {exc.reason}")
        return "refused"
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

    if not SHOPIFY_WEBHOOK_SECRET:
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
                extra={
                    "customer_email": customer_email,
                    "customer_name": customer_name,
                    "customer_id": order.get("customer", {}).get("id") if isinstance(order.get("customer"), dict) else None,
                },
                revenue_type="one_time",
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
        if v == "refused":
            return "refused — approval required (POST /contracts/send with approval_id)"
        if v is None:
            return "error"
        return "ok"

    return jsonify({"status": "processed", "order_id": order_id, "integrations": {
        k: _result_label(v) for k, v in results.items()
    }})


# --- Approved contract send (GAR-530) ---

@app.route("/contracts/send", methods=["POST"])
def contracts_send():
    """Send the DocuSign contract for an order once Garrett has issued a send.contract approval.

    Body: {"order_id", "email", "name", "approval_id"}. The approval must be scoped to that email.
    """
    body = request.get_json(force=True, silent=True) or {}
    email = str(body.get("email") or "").strip()
    if not email:
        return jsonify({"error": "email is required"}), 400
    result = _docusign_send_contract(
        body.get("order_id", ""), email, str(body.get("name") or "Unknown"),
        approval_id=body.get("approval_id"),
    )
    if result == "refused":
        return jsonify({"status": "refused", "error": "approval required"}), 403
    if result == "skipped":
        return jsonify({"status": "skipped", "error": "DocuSign not configured"}), 503
    if result is None:
        return jsonify({"status": "error"}), 502
    return jsonify({"status": "sent"})


# --- Integrations Status ---

@app.route("/integrations/status")
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
