"""API-key auth + simple rate limit for internal/AI endpoints (hardening audit, Oct 2026).

- The expected key comes from the APEX_API_KEY env var, read on every request.
  If it is missing or blank the endpoint returns 503 (fail closed).
- Callers send it as the X-API-Key header or "Authorization: Bearer <key>".
  Wrong or missing key -> 401. Comparison is constant-time (hmac.compare_digest).
- Each (key, client IP) pair may make APEX_RATE_LIMIT_PER_MIN requests per
  60 seconds (default 30). Over the limit -> 429 with a Retry-After header.
  Failed auth attempts are also counted per IP, which slows down key guessing.
  The limiter is in-memory, so it is per process/replica.
- Client IP is X-Real-IP when present. Railway's edge overwrites that header,
  so it is the connecting client rather than the proxy peer. X-Forwarded-For
  is ignored because a caller can prepend values.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import jsonify, request

API_KEY_ENV = "APEX_API_KEY"
RATE_LIMIT_ENV = "APEX_RATE_LIMIT_PER_MIN"
DEFAULT_RATE_LIMIT_PER_MIN = 30
WINDOW_SECONDS = 60.0

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)


def reset_rate_limits() -> None:
    """Clear limiter state (used by tests)."""
    with _lock:
        _hits.clear()


def _limit() -> int:
    try:
        value = int(os.environ.get(RATE_LIMIT_ENV, "") or DEFAULT_RATE_LIMIT_PER_MIN)
    except ValueError:
        value = DEFAULT_RATE_LIMIT_PER_MIN
    return max(1, value)


def _client_ip() -> str:
    # Railway's edge overwrites X-Real-IP with the connecting client (and with
    # Cf-Connecting-IP when Cloudflare sits in front). The socket peer is the
    # proxy, so REMOTE_ADDR would collapse every caller into one bucket.
    # X-Forwarded-For is not used: a caller can prepend spoofed addresses.
    real = (request.headers.get("X-Real-IP") or "").split(",")[0].strip()
    if real:
        return real
    return request.remote_addr or "unknown"


def _presented_key() -> str:
    key = (request.headers.get("X-API-Key") or "").strip()
    if not key:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    return key


def _sweep_expired(now: float) -> None:
    """Expire timestamps and delete empty buckets. Caller holds _lock."""
    empty = [bucket for bucket, q in _hits.items() if not q or now - q[0] >= WINDOW_SECONDS]
    for bucket in empty:
        q = _hits.get(bucket)
        if q is None:
            continue
        while q and now - q[0] >= WINDOW_SECONDS:
            q.popleft()
        if not q:
            del _hits[bucket]


def _over_limit(bucket: str) -> float:
    """Record a hit for bucket; return seconds to wait if over the limit, else 0."""
    now = time.monotonic()
    limit = _limit()
    with _lock:
        _sweep_expired(now)
        q = _hits[bucket]
        if len(q) >= limit:
            return max(1.0, WINDOW_SECONDS - (now - q[0]))
        q.append(now)
    return 0.0


def _too_many(wait: float):
    resp = jsonify({"error": "rate limit exceeded"})
    resp.status_code = 429
    resp.headers["Retry-After"] = str(int(wait) + 1)
    return resp


def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = (os.environ.get(API_KEY_ENV) or "").strip()
        if not expected:
            return jsonify({"error": f"service locked: {API_KEY_ENV} is not configured"}), 503
        ip = _client_ip()
        presented = _presented_key()
        if not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
            wait = _over_limit(f"badauth:{ip}")
            if wait:
                return _too_many(wait)
            resp = jsonify({"error": "invalid or missing API key"})
            resp.status_code = 401
            resp.headers["WWW-Authenticate"] = "Bearer"
            return resp
        key_id = hashlib.sha256(presented.encode()).hexdigest()[:16]  # never store the raw key
        wait = _over_limit(f"ok:{key_id}:{ip}")
        if wait:
            return _too_many(wait)
        return fn(*args, **kwargs)
    return wrapper
