"""GAR-530 approval gate: no dial, send, or charge without a Garrett-issued approval.

Stdlib only. Fails closed: on any doubt ``require_approval`` raises
``ApprovalRequired`` and writes a ``policy.action.refused.v1`` audit event.
There is deliberately no environment variable or flag that turns the gate off.

Storage (all JSONL, all outside git):
  APPROVALS_PATH        signed approval + revocation records (written only by the CLI)
  APPROVALS_USED_PATH   append-only use log (default: <APPROVALS_PATH>.used.jsonl sibling)
  APPROVAL_EVENTS_PATH  audit events, MARS envelope v1.1 shape (default: sibling policy_events.jsonl)

Keys (never committed, never logged):
  APPROVAL_VERIFY_KEY   HMAC-SHA256 key services use to verify records
  APPROVAL_SIGNING_KEY  key the CLI uses to sign records (Garrett only; same value with HMAC)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:  # POSIX advisory locks make max_uses safe across processes.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore

DEFAULT_PRODUCER = "apex-revenue-system"
SCHEMA_VERSION = "v1.1"
MIN_KEY_LEN = 32

SEND_ACTIONS = {"send.email", "send.sms", "send.message", "send.contract"}
DIAL_ACTIONS = {"dial.call"}
CHARGE_ACTIONS = {"charge.payment_intent", "charge.checkout"}
MONEY_ACTIONS = {"money.transfer"}
ACTIONS = frozenset(SEND_ACTIONS | DIAL_ACTIONS | CHARGE_ACTIONS | MONEY_ACTIONS)

# Only customer-initiated checkout (the buyer clicks Pay) may use a standing approval.
STANDING_ACTIONS = frozenset({"charge.checkout"})

# Request fields each action must supply AND the approval's scope must constrain.
REQUIRED_SCOPE: Dict[str, frozenset] = {
    **{a: frozenset({"to"}) for a in SEND_ACTIONS | DIAL_ACTIONS},
    "charge.payment_intent": frozenset({"amount_cents"}),
    "money.transfer": frozenset({"amount_cents"}),
    "charge.checkout": frozenset({"plan"}),
}


class ApprovalRequired(PermissionError):
    """Raised whenever an action is not provably approved."""

    def __init__(self, action: str, reason: str, policy_decision_id: Optional[str] = None):
        self.action = action
        self.reason = reason
        self.policy_decision_id = policy_decision_id
        super().__init__(f"approval required for {action}: {reason}")


# ── paths & keys ──────────────────────────────────────────────────────────────

def approvals_path() -> Path:
    return Path(os.getenv("APPROVALS_PATH") or Path.cwd() / "approvals" / "approvals.jsonl")


def used_path() -> Path:
    env = os.getenv("APPROVALS_USED_PATH")
    if env:
        return Path(env)
    p = approvals_path()
    return p.with_name(p.stem + ".used.jsonl")


def events_path() -> Path:
    env = os.getenv("APPROVAL_EVENTS_PATH") or os.getenv("EVENTS_PATH")
    if env:
        return Path(env)
    return approvals_path().with_name("policy_events.jsonl")


def _key(var: str) -> Optional[bytes]:
    val = os.getenv(var, "")
    if len(val) < MIN_KEY_LEN:
        return None
    return val.encode("utf-8")


# ── signing ───────────────────────────────────────────────────────────────────

def canonical(record: Dict[str, Any]) -> bytes:
    body = {k: v for k, v in record.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign(record: Dict[str, Any], key: bytes) -> str:
    return hmac.new(key, canonical(record), hashlib.sha256).hexdigest()


def _sig_ok(record: Dict[str, Any], key: bytes) -> bool:
    sig = record.get("sig")
    if not isinstance(sig, str):
        return False
    return hmac.compare_digest(sig, sign(record, key))


# ── time helpers ──────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def parse_duration(text: str) -> timedelta:
    """'90m', '24h', '7d' -> timedelta."""
    units = {"m": "minutes", "h": "hours", "d": "days"}
    text = text.strip().lower()
    if len(text) < 2 or text[-1] not in units or not text[:-1].isdigit():
        raise ValueError(f"bad duration {text!r}; use e.g. 30m, 24h, 7d")
    n = int(text[:-1])
    if n <= 0:
        raise ValueError("duration must be positive")
    return timedelta(**{units[text[-1]]: n})


def new_decision_id() -> str:
    return "pd_" + secrets.token_hex(13)


def target_hash(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


# ── store ─────────────────────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Raises OSError if the file is missing/unreadable (callers fail closed)."""
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # malformed lines can never authorize anything
            if isinstance(obj, dict):
                out.append(obj)
    return out


def load_records(key: bytes) -> Dict[str, Any]:
    """Return {'grants': {id: record}, 'revoked': set(ids)} using only validly signed lines."""
    grants: Dict[str, Dict[str, Any]] = {}
    revoked = set()
    for rec in _read_jsonl(approvals_path()):
        if not _sig_ok(rec, key):
            continue
        pid = rec.get("policy_decision_id")
        if not isinstance(pid, str) or not pid:
            continue
        if rec.get("type") == "revoke":
            revoked.add(pid)
        elif rec.get("type") == "grant" and pid not in grants:
            grants[pid] = rec  # first signed grant wins; duplicates can't widen scope
    return {"grants": grants, "revoked": revoked}


def _count_uses(pid: str) -> int:
    p = used_path()
    if not p.exists():
        return 0
    return sum(1 for r in _read_jsonl(p) if r.get("policy_decision_id") == pid)


# ── checks ────────────────────────────────────────────────────────────────────

def _norm(s: Any) -> str:
    return str(s).strip().lower()


def _check_scope(action: str, scope: Any, request: Dict[str, Any]) -> Optional[str]:
    """Return a refusal reason, or None if the request fits the approval scope."""
    if not isinstance(scope, dict):
        return "bad_scope"
    for key in REQUIRED_SCOPE.get(action, ()):  # request must say what it is doing
        if request.get(key) in (None, ""):
            return f"missing_{key}"
        if key == "amount_cents":
            if "max_amount_cents" not in scope:
                return "scope_missing_max_amount_cents"
        elif key not in scope:
            return f"scope_missing_{key}"

    to = request.get("to")
    if to not in (None, "") and "to" in scope:
        allowed = scope["to"]
        if not isinstance(allowed, list) or _norm(to) not in {_norm(x) for x in allowed}:
            return "recipient_not_approved"

    plan = request.get("plan")
    if plan not in (None, "") and "plan" in scope:
        allowed_plans = scope["plan"] if isinstance(scope["plan"], list) else [scope["plan"]]
        if _norm(plan) not in {_norm(p) for p in allowed_plans}:
            return "plan_not_approved"

    amount = request.get("amount_cents")
    if amount is None and "max_amount_cents" in scope:
        return "missing_amount_cents"  # a capped approval never covers an unstated amount
    if amount is not None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            return "bad_amount"
        if "max_amount_cents" in scope:
            cap = scope["max_amount_cents"]
            if isinstance(cap, bool) or not isinstance(cap, int) or amount > cap:
                return "amount_over_cap"

    currency = request.get("currency")
    if "currency" in scope:
        if currency in (None, ""):
            return "missing_currency"  # a currency-scoped approval never covers an unstated currency
        if _norm(currency) != _norm(scope["currency"]):
            return "currency_mismatch"

    return None


def _check_record(rec: Dict[str, Any], action: str, request: Dict[str, Any], revoked: set) -> Optional[str]:
    pid = rec["policy_decision_id"]
    if pid in revoked:
        return "revoked"
    if rec.get("action") != action:
        return "wrong_action"
    exp = _parse(rec.get("expires_at"))
    if exp is None:
        return "bad_expiry"
    if _now() >= exp:
        return "expired"
    standing = rec.get("standing") is True
    max_uses = rec.get("max_uses")
    if standing:
        if action not in STANDING_ACTIONS or max_uses is not None:
            return "standing_not_allowed"
    elif isinstance(max_uses, bool) or not isinstance(max_uses, int) or max_uses < 1:
        return "bad_max_uses"
    return _check_scope(action, rec.get("scope"), request)


# ── audit ─────────────────────────────────────────────────────────────────────

def build_event(decision: str, action: str, reason: str, *, site: str,
                policy_decision_id: Optional[str], request: Dict[str, Any],
                tenant_id: Optional[str] = None, correlation_id: Optional[str] = None) -> Dict[str, Any]:
    event_id = str(uuid.uuid4())
    payload = {
        "action": action,
        "decision": decision,
        "reason": reason,
        "site": site,
        "target_hash": target_hash(request.get("to")),
    }
    for k in ("amount_cents", "currency", "plan"):
        if request.get(k) is not None:
            payload[k] = request[k]
    return {
        "event_id": event_id,
        "event_type": f"policy.action.{decision}.v1",
        "schema_version": SCHEMA_VERSION,
        "producer": os.getenv("APPROVAL_PRODUCER") or DEFAULT_PRODUCER,
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "idempotency_key": f"policy-{event_id}",
        "classification": "restricted",
        "occurred_at": _iso(_now()),
        "tenant_id": tenant_id or os.getenv("GARCAR_TENANT_ID") or "garcar",
        "policy_decision_id": policy_decision_id,
        "evidence_refs": [f"approval:{policy_decision_id}"] if policy_decision_id else [],
        "payload": payload,
    }


def _append(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _emit(event: Dict[str, Any]) -> bool:
    try:
        _append(events_path(), event)
        return True
    except OSError:
        return False


def _refuse(action: str, reason: str, site: str, request: Dict[str, Any],
            pid: Optional[str] = None, correlation_id: Optional[str] = None) -> ApprovalRequired:
    _emit(build_event("refused", action, reason, site=site, policy_decision_id=pid,
                      request=request, correlation_id=correlation_id))
    return ApprovalRequired(action, reason, pid)


def _allow(action: str, rec: Dict[str, Any], site: str, request: Dict[str, Any],
           correlation_id: Optional[str]) -> Dict[str, Any]:
    pid = rec["policy_decision_id"]
    ok = _emit(build_event("allowed", action, "approved", site=site, policy_decision_id=pid,
                           request=request, tenant_id=rec.get("tenant_id"),
                           correlation_id=correlation_id))
    if not ok:  # no audit trail, no action
        raise ApprovalRequired(action, "audit_unavailable", pid)
    return dict(rec)


# ── public API ────────────────────────────────────────────────────────────────

def _request(to, amount_cents, currency, plan) -> Dict[str, Any]:
    return {"to": to, "amount_cents": amount_cents, "currency": currency, "plan": plan}


def require_approval(action: str, approval_id: Optional[str], *, to: Optional[str] = None,
                     amount_cents: Optional[int] = None, currency: Optional[str] = None,
                     plan: Optional[str] = None, site: str = "unknown",
                     correlation_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the approval record or raise ApprovalRequired. Never returns on doubt.

    A successful call consumes one use of a per-action approval.
    """
    req = _request(to, amount_cents, currency, plan)
    if action not in ACTIONS:
        raise _refuse(action, "unknown_action", site, req, correlation_id=correlation_id)
    if not approval_id or not isinstance(approval_id, str):
        raise _refuse(action, "missing_id", site, req, correlation_id=correlation_id)
    key = _key("APPROVAL_VERIFY_KEY")
    if key is None:
        raise _refuse(action, "verify_key_unavailable", site, req, approval_id, correlation_id)
    if fcntl is None:
        raise _refuse(action, "lock_unavailable", site, req, approval_id, correlation_id)

    try:
        lock_target = used_path()
        lock_target.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_target.with_name(lock_target.name + ".lock"), "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                store = load_records(key)
                rec = store["grants"].get(approval_id)
                if rec is None:
                    raise _refuse(action, "unknown_id", site, req, approval_id, correlation_id)
                reason = _check_record(rec, action, req, store["revoked"])
                if reason:
                    raise _refuse(action, reason, site, req, approval_id, correlation_id)
                if rec.get("standing") is not True:
                    if _count_uses(approval_id) >= rec["max_uses"]:
                        raise _refuse(action, "used_up", site, req, approval_id, correlation_id)
                    _append(used_path(), {"policy_decision_id": approval_id, "used_at": _iso(_now()),
                                          "action": action, "site": site,
                                          "target_hash": target_hash(to)})
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except ApprovalRequired:
        raise
    except OSError:
        raise _refuse(action, "store_unavailable", site, req, approval_id, correlation_id)
    return _allow(action, rec, site, req, correlation_id)


def require_standing_approval(action: str, *, plan: str, amount_cents: Optional[int] = None,
                              currency: Optional[str] = None, site: str = "unknown",
                              correlation_id: Optional[str] = None) -> Dict[str, Any]:
    """Customer-initiated checkout: find Garrett's standing approval for this plan.

    One standing approval per plan (long expiry, unlimited uses, scope=plan [+ price cap]).
    """
    req = _request(None, amount_cents, currency, plan)
    if action not in STANDING_ACTIONS:
        raise _refuse(action, "standing_not_allowed", site, req, correlation_id=correlation_id)
    key = _key("APPROVAL_VERIFY_KEY")
    if key is None:
        raise _refuse(action, "verify_key_unavailable", site, req, correlation_id=correlation_id)
    try:
        store = load_records(key)
    except OSError:
        raise _refuse(action, "store_unavailable", site, req, correlation_id=correlation_id)
    last_reason = "no_standing_approval"
    for rec in store["grants"].values():
        if rec.get("standing") is not True or rec.get("action") != action:
            continue
        reason = _check_record(rec, action, req, store["revoked"])
        if reason is None:
            return _allow(action, rec, site, req, correlation_id)
        last_reason = reason
    raise _refuse(action, last_reason, site, req, correlation_id=correlation_id)


def make_grant(*, action: str, scope: Dict[str, Any], expires_in: timedelta,
               max_uses: Optional[int], standing: bool = False, approver: str = "garrett",
               tenant_id: str = "garcar", note: str = "") -> Dict[str, Any]:
    """Build an unsigned grant record (the CLI signs and appends it)."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; choose from {sorted(ACTIONS)}")
    if standing:
        if action not in STANDING_ACTIONS:
            raise ValueError("standing approvals are only allowed for charge.checkout")
        max_uses = None
    elif not isinstance(max_uses, int) or max_uses < 1:
        raise ValueError("per-action approvals need max_uses >= 1")
    now = _now()
    return {
        "type": "grant",
        "policy_decision_id": new_decision_id(),
        "action": action,
        "scope": scope,
        "standing": standing,
        "max_uses": max_uses,
        "approver": approver,
        "tenant_id": tenant_id,
        "issued_at": _iso(now),
        "expires_at": _iso(now + expires_in),
        "note": note,
    }


def append_signed(record: Dict[str, Any], key: bytes) -> Dict[str, Any]:
    rec = dict(record)
    rec["sig"] = sign(rec, key)
    _append(approvals_path(), rec)
    return rec
