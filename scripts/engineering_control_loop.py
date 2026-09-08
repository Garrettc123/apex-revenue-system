#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import PurePosixPath
from pathlib import Path

OWNER, REPO = os.environ.get("GITHUB_REPOSITORY", "").split("/", 1)
TOKEN = os.environ.get("GITHUB_TOKEN", "")
API = "https://api.github.com"
OUT = Path(os.environ.get("REPORT_DIR", "engineering-reports"))
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
IMPORTANT = {"README.md", "main.py", "app.py", "server.py", "requirements.txt", "pyproject.toml", "Dockerfile", "railway.toml", "vercel.json"}


def api(path, raw=False):
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github.raw+json" if raw else "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if TOKEN: req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode() if raw else json.loads(r.read().decode())


def openai_review(snapshot):
    if not OPENAI_KEY:
        return {"enabled": False, "message": "OPENAI_API_KEY not configured; deterministic review completed."}
    prompt = """Act as a senior software architect and defensive security reviewer. Analyze the supplied repository snapshot. Do not invent vulnerabilities. Return JSON with architecture_summary, top_risks, bug_priorities, launch_blockers, recommended_next_actions. Each risk must include severity, evidence and action. Never recommend destructive autonomous changes."""
    payload = json.dumps({"model": MODEL, "input": prompt + "\nSNAPSHOT:\n" + json.dumps(snapshot)[:90000]}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=payload, method="POST")
    req.add_header("Content-Type", "application/json"); req.add_header("Authorization", f"Bearer {OPENAI_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=90) as r: result = json.loads(r.read().decode())
        text = result.get("output_text", "")
        if not text:
            text = "".join(c.get("text", "") for i in result.get("output", []) for c in c.get("content", []) if c.get("type") == "output_text")
        try: return {"enabled": True, "model": MODEL, "analysis": json.loads(text)}
        except Exception: return {"enabled": True, "model": MODEL, "analysis_text": text}
    except Exception as e: return {"enabled": True, "model": MODEL, "error": str(e)}


def main():
    meta = api(f"/repos/{OWNER}/{REPO}"); branch = meta.get("default_branch", "main")
    tree_data = api(f"/repos/{OWNER}/{REPO}/git/trees/{branch}?recursive=1")
    tree = [x for x in tree_data.get("tree", []) if not any(p in EXCLUDED for p in PurePosixPath(x.get("path", "")).parts)]
    files = [x for x in tree if x.get("type") == "blob"]
    ext = Counter(PurePosixPath(x["path"]).suffix.lower() or "[none]" for x in files)
    important = []
    for x in files:
        p = x["path"]; name = PurePosixPath(p).name
        if name in IMPORTANT or ".github/workflows/" in p:
            try: text = api(f"/repos/{OWNER}/{REPO}/contents/{p}?ref={branch}", raw=True)[:8000]
            except Exception: text = ""
            important.append({"path": p, "size": x.get("size", 0), "excerpt": text})
    issues = api(f"/repos/{OWNER}/{REPO}/issues?state=open&per_page=100")
    triage = []
    for i in issues:
        if "pull_request" in i: continue
        text = (i.get("title", "") + "\n" + (i.get("body") or "")).lower(); score = 0; reasons=[]
        rules = [(40,["security","vulnerability","secret","credential","auth"],"security"),(35,["payment","stripe","billing","checkout","revenue"],"revenue"),(30,["production","outage","down","data loss","crash"],"production"),(20,["bug","error","failure","broken","exception"],"defect"),(10,["deploy","deployment","ci","workflow"],"delivery")]
        for weight, terms, label in rules:
            hits=[t for t in terms if t in text]
            if hits: score += weight; reasons.append({"category":label,"matches":hits})
        score += min(i.get("comments",0) or 0,10)
        severity="critical" if score>=60 else "high" if score>=35 else "medium" if score>=15 else "low"
        triage.append({"number":i["number"],"title":i["title"],"url":i["html_url"],"score":score,"severity":severity,"reasons":reasons})
    triage.sort(key=lambda x:(-x["score"],x["number"]))
    try: runs=api(f"/repos/{OWNER}/{REPO}/actions/runs?branch={branch}&status=failure&per_page=30").get("workflow_runs",[])
    except Exception: runs=[]
    failed=[{"id":r["id"],"name":r.get("name"),"run_number":r.get("run_number"),"created_at":r.get("created_at"),"url":r.get("html_url")} for r in runs]
    text="\n".join(x["excerpt"] for x in important).lower(); paths=[x["path"].lower() for x in files]
    def check(name, ok, severity="high", evidence=""): return {"check":name,"status":"PASS" if ok else "REVIEW","severity":severity,"evidence":evidence}
    security=[
      check("Secrets excluded from source",not any(re.search(r"(^|/)(\.env|.*\.pem|.*\.key)$",p) for p in paths),evidence="Common secret-bearing filenames scan."),
      check("Dependency manifest present",any(PurePosixPath(p).name in IMPORTANT for p in paths),"medium","Manifest/build file detected."),
      check("CI/tests present",any("test" in p and p.endswith((".py",".yml",".yaml")) for p in paths),"medium","Test files/workflows detected."),
      check("Webhook signature verification","hmac" in text or "signature" in text,evidence="HMAC/signature terminology detected."),
      check("Payment idempotency","idempot" in text or "seen_charge" in text,evidence="Idempotency implementation detected."),
      check("Authentication boundary",any(k in text for k in ["authorization","bearer","api key","auth"]),evidence="Authentication-related controls detected."),
      check("Input validation",any(k in text for k in ["pydantic","validate","required","missing fields"]),"medium","Validation patterns detected."),
      check("Audit logging",any(k in text for k in ["audit","ledger","event_log","logging"]),"medium","Audit/event logging detected."),
      check("External side-effect approval","approval" in text or "human" in text,evidence="Approval/human-control terminology detected."),
      check("Health endpoint","/health" in text or "def health" in text,"medium","Health endpoint detected."),
    ]
    base={"repository":f"{OWNER}/{REPO}","default_branch":branch,"generated_at":datetime.now(timezone.utc).isoformat(),"codebase":{"file_count":len(files),"extensions":ext.most_common(20),"important_files":important},"issues":triage,"failed_runs":failed,"security":security}
    report={**base,"ai_review":openai_review(base)}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/"report.json").write_text(json.dumps(report,indent=2));
    lines=[f"# Engineering Control Loop — {OWNER}/{REPO}",f"Generated: {report['generated_at']}","","## Status",f"- Files: **{len(files)}**",f"- Open issues: **{len(triage)}**",f"- Failed Actions runs: **{len(failed)}**",f"- Security reviews: **{sum(x['status']=='REVIEW' for x in security)}**","","## Codebase context","### File types"]
    lines += [f"- `{k}`: {v}" for k,v in ext.most_common(12)]; lines += ["","### Important files"]+[f"- `{x['path']}`" for x in important[:40]]
    lines += ["","## Bug triage","| Severity | Score | Issue |","|---|---:|---|"]+[f"| {x['severity'].upper()} | {x['score']} | [#{x['number']} {x['title']}]({x['url']}) |" for x in triage[:30]]
    lines += ["","## Failed CI/CD runs"]+[f"- [{x['name']} #{x['run_number']}]({x['url']}) — {x['created_at']}" for x in failed[:30]] or ["- None detected"]
    lines += ["","## Launch security gate","| Check | Status | Severity | Evidence |","|---|---|---|---|"]+[f"| {x['check']} | **{x['status']}** | {x['severity']} | {x['evidence']} |" for x in security]
    ai=report["ai_review"]; lines += ["","## AI review"]
    if ai.get("analysis"):
        a=ai["analysis"]; lines.append(a.get("architecture_summary","")); lines += ["", "### Top risks"]+[f"- **{x.get('severity','').upper()}** — {x.get('risk','')} — {x.get('action','')}" for x in a.get("top_risks",[])]; lines += ["", "### Launch blockers"]+[f"- {x}" for x in a.get("launch_blockers",[])]; lines += ["", "### Next actions"]+[f"- {x}" for x in a.get("recommended_next_actions",[])]
    else: lines.append(ai.get("message") or ai.get("error") or ai.get("analysis_text", "No AI review."))
    (OUT/"report.md").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))
    if os.environ.get("FAIL_ON_SECURITY_REVIEW","false").lower()=="true" and any(x["status"]=="REVIEW" and x["severity"]=="high" for x in security): raise SystemExit("Launch gate blocked: high-severity security review required")

if __name__ == "__main__": main()
