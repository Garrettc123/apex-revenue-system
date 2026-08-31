from flask import Flask, jsonify, request, render_template, redirect
import datetime, os, json, hmac, hashlib, math, tempfile
from pathlib import Path
import fcntl
import requests as http
from google import genai as _genai

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
        "coinbase": "connected" if CB_API_KEY else "set COINBASE_API_KEY"
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
