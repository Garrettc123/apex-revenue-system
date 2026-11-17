#!/usr/bin/env python3
"""Export a minimal SQLite swarm-health snapshot; optionally push to review branch.

Zero third-party Python dependencies. Never exports event IDs, free-text event
contents, DB rows, hostnames, customer data, credentials or wallet addresses.
The destination repository is PUBLIC. Default mode never pushes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import quote

EXPECTED_REPO = "Garrettc123/systems-master-hub"
EXPORT_RELATIVE = Path("telemetry/public_swarm_snapshot.json")
SCHEMA = "garcar.public_swarm_aggregate.v1"
TABLES = {
    "task_queue": ("status",),
    "event_ledger": ("event_id", "event_type", "status", "timestamp"),
    "dead_letter_quarantine": (),
}
STATUS_CLASSES = frozenset({
    "pending", "queued", "running", "in_progress", "retrying",
    "paused", "blocked", "succeeded", "completed", "failed", "cancelled"
})
BRANCH_RE = re.compile(r"^telemetry/swarm-[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class SyncError(RuntimeError):
    pass


SKIP_DIRS = frozenset({
    ".git", ".cache", ".local", ".venv", "venv", "node_modules",
    "__pycache__", "storage", "Android", "Downloads", "tmp",
})
DB_FILENAME = "garcar_forge_operational.db"


def discover_databases(*, roots=None, max_depth=4) -> list[Path]:
    """Find only exact database filenames in bounded operator workspaces.

    Directory names are not printed or uploaded unless the user runs local
    --locate-db. No file contents are opened during discovery.
    """
    if roots is None:
        home = Path.home()
        roots = [
            Path.cwd(), home / "apex-revenue-system",
            home / "systems-master-hub", home / "systems-master-hub-sync",
            home / "ZEUS" / "repos", home / "garcar-enterprise",
        ]
    found: dict[str, Path] = {}
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir() or root.is_symlink():
            continue
        for current, directories, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            directories[:] = [
                name for name in directories
                if depth < max_depth and name not in SKIP_DIRS
                and not (current_path / name).is_symlink()
            ]
            if DB_FILENAME in filenames:
                candidate = current_path / DB_FILENAME
                if candidate.is_file() and not candidate.is_symlink():
                    resolved = candidate.resolve()
                    found[str(resolved)] = resolved
    return sorted(found.values(), key=str)


def resolve_database(db: Path, *, roots=None) -> Path:
    """Require one unambiguous existing source; never create an empty DB."""
    if str(db) != "auto":
        return db.expanduser()
    paths = discover_databases(roots=roots)
    if not paths:
        raise SyncError(
            "No garcar_forge_operational.db found in the bounded search; "
            "use --locate-db or pass its absolute location with --db"
        )
    if len(paths) != 1:
        raise SyncError(
            "Multiple matching databases found. Set --db to the intended "
            "absolute path after inspecting --locate-db"
        )
    return paths[0]


def _counts(values):
    """Allowlisted status labels; all other labels collapsed into 'other'."""
    result = {}
    for label, count in values:
        key = str(label).casefold() if label is not None else "other"
        if key not in STATUS_CLASSES:
            key = "other"
        result[key] = result.get(key, 0) + int(count)
    return dict(sorted(result.items()))


def snapshot(db_path: Path, *, today: datetime | None = None) -> dict:
    source = db_path.expanduser().resolve(strict=True)
    if not source.is_file():
        raise SyncError("database is not a regular file")
    at = today or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise SyncError("timestamp must be timezone-aware")

    # URI mode=ro plus query_only prevents creating or updating the DB. A
    # read transaction provides a consistent snapshot with concurrent WAL writers.
    uri = "file:" + quote(str(source), safe="/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise SyncError("SQLite read-only connection failed") from exc
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        check_rows = conn.execute("PRAGMA quick_check(1)").fetchall()
        integrity = "ok" if check_rows == [("ok",)] else "failed"
        known_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        coverage = {}
        warnings = []
        for table, columns in TABLES.items():
            if table not in known_tables:
                coverage[table] = {"state": "missing"}
                warnings.append("missing_table:" + table)
                continue
            actual = {row[1] for row in conn.execute(
                'PRAGMA table_info("' + table + '")'
            ).fetchall()}
            if not set(columns).issubset(actual):
                coverage[table] = {"state": "schema_mismatch"}
                warnings.append("schema_mismatch:" + table)
                continue
            if table == "task_queue":
                values = conn.execute(
                    "SELECT status, COUNT(*) FROM task_queue GROUP BY status"
                ).fetchall()
                coverage[table] = {"state": "present", "status_counts": _counts(values)}
            elif table == "event_ledger":
                # At most ten event statuses are read. Never publish IDs, types,
                # timestamps, messages, or exact records.
                values = conn.execute(
                    "SELECT status FROM event_ledger ORDER BY timestamp DESC LIMIT 10"
                ).fetchall()
                coverage[table] = {
                    "state": "present",
                    "latest_ten_status_counts": _counts(
                        [(name, sum(1 for (s,) in values if s == name))
                         for name in {s for (s,) in values}]
                    ),
                    "window_size": len(values),
                }
            else:
                count = conn.execute(
                    "SELECT COUNT(*) FROM dead_letter_quarantine"
                ).fetchone()[0]
                coverage[table] = {"state": "present", "count": int(count)}
        conn.execute("ROLLBACK")
    except sqlite3.Error as exc:
        raise SyncError("SQLite inspection failed; snapshot not published") from exc
    finally:
        conn.close()

    if integrity != "ok":
        warnings.append("integrity_check_failed")
    complete = integrity == "ok" and all(
        value["state"] == "present" for value in coverage.values()
    )
    data = {
        "schema": SCHEMA,
        "captured_utc_date": at.astimezone(timezone.utc).date().isoformat(),
        "source": "operator_local_sqlite_read_only",
        "integrity": integrity,
        "coverage": coverage,
        "complete": complete,
        "warnings": sorted(warnings),
        "claims": {
            "revenue_verified": False,
            "bounty_payouts_verified": False,
            "investor_valuation_established": False,
        },
        "privacy": "allowlisted_aggregate_only_no_identifiers",
    }
    # This is a content digest, NOT a digital signature or trusted attestation.
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    data["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return data


def atomic_export(repo_path: Path, data: dict) -> Path:
    root = repo_path.expanduser().resolve(strict=True)
    if not (root / ".git").exists():
        raise SyncError("repository path is not a Git working tree root")
    target = root / EXPORT_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise SyncError("export target must not be a symlink")
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=".snapshot-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def _git(repo_path: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_path), *args], check=True,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30,
        )
        return completed.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise SyncError("Git operation failed; inspect local Git status") from exc


def publish_review_branch(repo_path: Path, data: dict, *, public_ack: bool) -> str:
    """Explicit opt-in only. Pushes a single allowlisted file, never main."""
    if not public_ack:
        raise SyncError("public repository requires --allow-public")
    if data.get("complete") is not True or data.get("schema") != SCHEMA:
        raise SyncError("incomplete, unhealthy or unknown schema cannot be published")
    root = repo_path.expanduser().resolve(strict=True)
    remote = _git(root, "remote", "get-url", "origin")
    accepted = {
        "https://github.com/" + EXPECTED_REPO + ".git",
        "https://github.com/" + EXPECTED_REPO,
        "git@github.com:" + EXPECTED_REPO + ".git",
        "git@github.com:" + EXPECTED_REPO,
    }
    if remote not in accepted:
        raise SyncError("unexpected Git remote; refusing to publish")
    branch = _git(root, "branch", "--show-current")
    if not BRANCH_RE.fullmatch(branch):
        raise SyncError("push requires a telemetry/swarm-YYYY-MM-DD review branch")
    # Do not push if any unrelated edits are present. Avoid folding other
    # staged changes into a telemetry commit.
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    for line in dirty.splitlines():
        if line[3:] != EXPORT_RELATIVE.as_posix():
            raise SyncError("unrelated working-tree changes; refusing push")
    _git(root, "add", "--", EXPORT_RELATIVE.as_posix())
    staged = _git(root, "diff", "--cached", "--name-only")
    if not staged:
        return branch  # idempotent: no change to commit or push
    if staged != EXPORT_RELATIVE.as_posix():
        raise SyncError("staging scope differs from expected telemetry file")
    _git(root, "commit", "-m", "chore(telemetry): aggregate SQLite health checkpoint",
         "--", EXPORT_RELATIVE.as_posix())
    _git(root, "push", "origin", "HEAD:refs/heads/" + branch)
    return branch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=Path("auto"), type=Path,
                        help="absolute SQLite file path, or auto (bounded local lookup)")
    parser.add_argument("--repo", default=None, type=Path,
                        help="required for export: systems-master-hub clone root")
    parser.add_argument("--locate-db", action="store_true",
                        help="print local matching DB paths, never open or publish them")
    parser.add_argument("--diagnose", action="store_true",
                        help="print redacted diagnostics; do not write or push")
    parser.add_argument("--push", action="store_true",
                        help="commit and push on a telemetry/ review branch")
    parser.add_argument("--allow-public", action="store_true",
                        help="acknowledge public disclosure of aggregate counts")
    args = parser.parse_args(argv)
    if args.diagnose and args.push:
        parser.error("--diagnose cannot be combined with --push")
    try:
        if args.locate_db:
            matches = discover_databases()
            for match in matches:
                print(match)
            if not matches:
                print("No matching database found in bounded workspaces.", file=sys.stderr)
                return 2
            return 0
        source = resolve_database(args.db)
        result = snapshot(source)
        if args.diagnose:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            if args.repo is None:
                raise SyncError("Export requires --repo pointing to systems-master-hub")
            root = args.repo.expanduser().resolve(strict=True)
            remote = _git(root, "remote", "get-url", "origin")
            if remote not in {
                "https://github.com/" + EXPECTED_REPO + ".git",
                "https://github.com/" + EXPECTED_REPO,
                "git@github.com:" + EXPECTED_REPO + ".git",
                "git@github.com:" + EXPECTED_REPO,
            }:
                raise SyncError("Export target must be systems-master-hub clone, not apex-revenue-system")
            path = atomic_export(root, result)
            print("Local aggregate written:", path)
        if args.push:
            print("Pushed review branch:",
                  publish_review_branch(args.repo, result, public_ack=args.allow_public))
        if not result["complete"]:
            print("WARNING: incomplete schema or failed integrity; do not treat as healthy",
                  file=sys.stderr)
            return 2
        return 0
    except (SyncError, FileNotFoundError, PermissionError) as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
