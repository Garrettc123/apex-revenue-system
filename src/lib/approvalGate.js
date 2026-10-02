/**
 * GAR-530 approval gate, minimal JS port for the Next.js checkout route.
 *
 * Only supports the STANDING checkout approval (charge.checkout, one per plan), which is
 * the only gated action in JS. It reads the same signed JSONL records that
 * `python -m approval_gate.cli grant` writes, and verifies them with the same HMAC-SHA256
 * canonical form as approval_gate/gate.py (a cross-language test keeps the two in sync).
 *
 * Fails closed: any doubt throws ApprovalRequired. There is no flag that turns it off.
 *
 * Config (env, never commit values):
 *   APPROVAL_VERIFY_KEY   HMAC key (>= 32 chars)
 *   APPROVALS_PATH        path to approvals.jsonl, or
 *   APPROVALS_JSONL       the signed record lines themselves (for serverless hosts such as
 *                         Vercel, where there is no persistent disk). Records are signed, not secret.
 *   APPROVAL_EVENTS_PATH  audit log (JSONL). If unset and APPROVALS_PATH is set, a sibling
 *                         policy_events.jsonl is used; otherwise events go to stdout as one
 *                         JSON line each (picked up by the host's log drain).
 *
 * CommonJS on purpose so plain `node` and Next.js can both load it.
 */
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const DEFAULT_PRODUCER = 'apex-revenue-system';
const SCHEMA_VERSION = 'v1.1';
const MIN_KEY_LEN = 32;
const STANDING_ACTIONS = new Set(['charge.checkout']);

class ApprovalRequired extends Error {
  constructor(action, reason, policyDecisionId = null) {
    super(`approval required for ${action}: ${reason}`);
    this.name = 'ApprovalRequired';
    this.action = action;
    this.reason = reason;
    this.policyDecisionId = policyDecisionId;
  }
}

// ── canonical JSON (must match Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)) ──

function asciiEscape(json) {
  return json.replace(/[\u007f-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
}

function stable(value) {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map((k) => JSON.stringify(k) + ':' + stable(value[k])).join(',') + '}';
  }
  if (value === undefined) return 'null';
  return JSON.stringify(value);
}

function canonical(record) {
  const body = {};
  for (const k of Object.keys(record)) if (k !== 'sig') body[k] = record[k];
  return Buffer.from(asciiEscape(stable(body)), 'utf8');
}

function sign(record, key) {
  return crypto.createHmac('sha256', key).update(canonical(record)).digest('hex');
}

function sigOk(record, key) {
  if (typeof record.sig !== 'string') return false;
  const want = Buffer.from(sign(record, key), 'utf8');
  const got = Buffer.from(record.sig, 'utf8');
  return want.length === got.length && crypto.timingSafeEqual(want, got);
}

// ── store ──

function verifyKey() {
  const v = process.env.APPROVAL_VERIFY_KEY || '';
  return v.length >= MIN_KEY_LEN ? Buffer.from(v, 'utf8') : null;
}

function approvalsPath() {
  return process.env.APPROVALS_PATH || path.join(process.cwd(), 'approvals', 'approvals.jsonl');
}

function readLines() {
  // Throws if nothing can be read: callers fail closed.
  const inline = process.env.APPROVALS_JSONL;
  const text = !process.env.APPROVALS_PATH && inline ? inline : fs.readFileSync(approvalsPath(), 'utf8');
  const out = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    try {
      const obj = JSON.parse(t);
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) out.push(obj);
    } catch (_) {
      // malformed lines can never authorize anything
    }
  }
  return out;
}

function loadRecords(key) {
  const grants = new Map();
  const revoked = new Set();
  for (const rec of readLines()) {
    if (!sigOk(rec, key)) continue;
    const pid = rec.policy_decision_id;
    if (typeof pid !== 'string' || !pid) continue;
    if (rec.type === 'revoke') revoked.add(pid);
    else if (rec.type === 'grant' && !grants.has(pid)) grants.set(pid, rec); // first signed grant wins
  }
  return { grants, revoked };
}

// ── checks (mirror approval_gate/gate.py for charge.checkout) ──

const norm = (s) => String(s).trim().toLowerCase();
const isInt = (n) => typeof n === 'number' && Number.isInteger(n);

function parseTs(ts) {
  if (typeof ts !== 'string' || !/(Z|[+-]\d\d:\d\d)$/.test(ts)) return null; // tz required, like Python
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

function checkScope(scope, req) {
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)) return 'bad_scope';
  if (req.plan === undefined || req.plan === null || req.plan === '') return 'missing_plan';
  if (!('plan' in scope)) return 'scope_missing_plan';
  const plans = Array.isArray(scope.plan) ? scope.plan : [scope.plan];
  if (!plans.map(norm).includes(norm(req.plan))) return 'plan_not_approved';
  if (req.amount_cents !== undefined && req.amount_cents !== null) {
    if (!isInt(req.amount_cents) || req.amount_cents <= 0) return 'bad_amount';
    if ('max_amount_cents' in scope && (!isInt(scope.max_amount_cents) || req.amount_cents > scope.max_amount_cents)) {
      return 'amount_over_cap';
    }
  }
  if ('currency' in scope && req.currency != null && norm(req.currency) !== norm(scope.currency)) return 'currency_mismatch';
  return null;
}

function checkRecord(rec, action, req, revoked) {
  if (revoked.has(rec.policy_decision_id)) return 'revoked';
  if (rec.action !== action) return 'wrong_action';
  const exp = parseTs(rec.expires_at);
  if (!exp) return 'bad_expiry';
  if (Date.now() >= exp.getTime()) return 'expired';
  if (rec.standing !== true || !STANDING_ACTIONS.has(action) || (rec.max_uses !== null && rec.max_uses !== undefined)) {
    return 'standing_not_allowed';
  }
  return checkScope(rec.scope, req);
}

// ── audit ──

function isoNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function buildEvent(decision, action, reason, { site, policyDecisionId, req, tenantId, correlationId }) {
  const eventId = crypto.randomUUID();
  const payload = { action, decision, reason, site, target_hash: null };
  for (const k of ['amount_cents', 'currency', 'plan']) if (req[k] !== undefined && req[k] !== null) payload[k] = req[k];
  return {
    event_id: eventId,
    event_type: `policy.action.${decision}.v1`,
    schema_version: SCHEMA_VERSION,
    producer: process.env.APPROVAL_PRODUCER || DEFAULT_PRODUCER,
    correlation_id: correlationId || crypto.randomUUID(),
    idempotency_key: `policy-${eventId}`,
    classification: 'restricted',
    occurred_at: isoNow(),
    tenant_id: tenantId || process.env.GARCAR_TENANT_ID || 'garcar',
    policy_decision_id: policyDecisionId || null,
    evidence_refs: policyDecisionId ? [`approval:${policyDecisionId}`] : [],
    payload,
  };
}

function eventsPath() {
  if (process.env.APPROVAL_EVENTS_PATH || process.env.EVENTS_PATH) {
    return process.env.APPROVAL_EVENTS_PATH || process.env.EVENTS_PATH;
  }
  if (process.env.APPROVALS_PATH) return path.join(path.dirname(process.env.APPROVALS_PATH), 'policy_events.jsonl');
  return null; // stdout
}

function emit(event) {
  const line = JSON.stringify(event);
  const p = eventsPath();
  try {
    if (p) {
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.appendFileSync(p, line + '\n', 'utf8');
    } else {
      process.stdout.write(line + '\n');
    }
    return true;
  } catch (_) {
    return false;
  }
}

function refuse(action, reason, ctx) {
  emit(buildEvent('refused', action, reason, ctx));
  return new ApprovalRequired(action, reason, ctx.policyDecisionId || null);
}

/**
 * Customer-initiated checkout: find Garrett's standing approval for this plan.
 * Returns the approval record, or throws ApprovalRequired (fail closed).
 */
function requireStandingApproval(action, { plan, amountCents, currency, site = 'unknown', correlationId } = {}) {
  const req = { amount_cents: amountCents, currency, plan };
  const ctx = { site, req, correlationId };
  if (!STANDING_ACTIONS.has(action)) throw refuse(action, 'standing_not_allowed', ctx);
  const key = verifyKey();
  if (!key) throw refuse(action, 'verify_key_unavailable', ctx);
  let store;
  try {
    store = loadRecords(key);
  } catch (_) {
    throw refuse(action, 'store_unavailable', ctx);
  }
  let lastReason = 'no_standing_approval';
  for (const rec of store.grants.values()) {
    if (rec.standing !== true || rec.action !== action) continue;
    const reason = checkRecord(rec, action, req, store.revoked);
    if (reason === null) {
      const ok = emit(buildEvent('allowed', action, 'approved', {
        site, req, correlationId, policyDecisionId: rec.policy_decision_id, tenantId: rec.tenant_id,
      }));
      if (!ok) throw new ApprovalRequired(action, 'audit_unavailable', rec.policy_decision_id); // no audit, no charge
      return { ...rec };
    }
    lastReason = reason;
  }
  throw refuse(action, lastReason, ctx);
}

module.exports = { ApprovalRequired, requireStandingApproval, canonical, sign };
