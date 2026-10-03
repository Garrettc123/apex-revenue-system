"""GAR-530 static guard: dial/send/charge code must go through the approval gate.

Fails if a source file contains an outbound send/charge/dial pattern but never
references the gate (``approval_gate`` in Python, ``approvalGate`` in JS). Also fails if
an auto-merge workflow is present. This is a tripwire for new code, not a proof.
"""
import re

import pytest

from gate_support import ROOT

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "tests", "approval_gate", ".next"}

PY_PATTERNS = re.compile(
    r"smtplib\.SMTP|\.sendmail\(|api\.sendgrid\.com|api\.resend\.com|api\.postmarkapp\.com"
    r"|stripe\.[A-Za-z_.]+\.create\(|stripe\.Transfer|stripe\.Payout|/v1/charges|commerce\.coinbase\.com"
    r"|docusign|api\.bland\.ai|bland\.ai/v1/calls|api\.twilio\.com|twilio\.rest|messages\.create\(",
    re.IGNORECASE,
)
JS_PATTERNS = re.compile(
    r"api\.sendgrid\.com|api\.resend\.com|@sendgrid/mail|resend\.emails\.send|nodemailer"
    r"|stripe\.(paymentIntents|checkout\.sessions|transfers|payouts|invoices|charges|subscriptions)\.create"
    r"|/v1/charges|commerce\.coinbase\.com|docusign|api\.bland\.ai|api\.twilio\.com",
    re.IGNORECASE,
)
MERGE_PATTERNS = re.compile(
    r"pascalgn/automerge-action|hmarr/auto-approve-action|gh pr merge|enable-pull-request-automerge"
    r"|peter-evans/enable-pull-request-automerge|--auto\b.*merge|merge_method",
    re.IGNORECASE,
)
# Removed by https://github.com/Garrettc123/apex-revenue-system/pull/19 (separate PR, merges first).
REMOVED_BY_PR19 = {"auto-approve-merge.yml", "auto-merge-copilot.yml"}


def _files(exts):
    for path in ROOT.rglob("*"):
        if path.suffix in exts and path.is_file() and not (set(path.relative_to(ROOT).parts) & SKIP_DIRS):
            yield path


def _offenders(exts, pattern, markers):
    bad = []
    for path in _files(exts):
        text = path.read_text(encoding="utf-8", errors="replace")
        m = pattern.search(text)
        if m and not any(marker in text for marker in markers):
            bad.append(f"{path.relative_to(ROOT)}: {m.group(0)}")
    return bad


def _merge_workflows():
    wf = ROOT / ".github" / "workflows"
    return {p.name for p in wf.glob("*.y*ml") if MERGE_PATTERNS.search(p.read_text())}


def test_python_send_charge_dial_sites_import_approval_gate():
    assert _offenders({".py"}, PY_PATTERNS, ("approval_gate",)) == []


def test_js_send_charge_dial_sites_use_approval_gate():
    assert _offenders({".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}, JS_PATTERNS, ("approvalGate", "approval_gate")) == []


def test_no_new_auto_merge_workflows():
    assert _merge_workflows() - REMOVED_BY_PR19 == set()


@pytest.mark.xfail(
    condition=bool(_merge_workflows() & REMOVED_BY_PR19),
    reason="auto-merge workflows still on this base; removed by PR #19", strict=True,
)
def test_no_auto_merge_workflows_at_all():
    assert _merge_workflows() == set()


def test_guard_actually_detects_ungated_code():
    assert PY_PATTERNS.search('http.post(f"https://api.commerce.coinbase.com/charges")')
    assert JS_PATTERNS.search("await stripe.checkout.sessions.create({})")
    assert PY_PATTERNS.search("stripe.checkout.Session.create(mode='payment')")


def test_codeowners_requires_garrett():
    text = (ROOT / ".github" / "CODEOWNERS").read_text()
    assert re.search(r"^\*\s+@Garrettc123\s*$", text, re.MULTILINE)
