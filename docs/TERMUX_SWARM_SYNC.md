# Termux SQLite Swarm Diagnostic — Apex entry point

The exact original command failed because `scripts/sync_to_master_hub.py` did not exist in the `apex-revenue-system` working tree. This review branch now includes a standalone, stdlib-only copy of the reviewed diagnostic/exporter, with the same safety contract as systems-master-hub PR #62.

## Install the review branch without overwriting local changes

From `~/apex-revenue-system`:

```sh
git status --short
git fetch origin fix/termux-swarm-sync-entrypoint-20261117
git switch --create fix/termux-swarm-sync-entrypoint-20261117 --track origin/fix/termux-swarm-sync-entrypoint-20261117
```

If the working tree contains uncommitted changes, save them safely before switching branches; do not force-reset.

### 1. Locate the actual database (no database writes)

```sh
python scripts/sync_to_master_hub.py --locate-db
```

The search is bounded to the current workspace and a few known local workspaces. It does **not** create a database. If nothing is found, provide the real absolute path with `--db`, or inspect the producer's database configuration. If multiple candidates exist, specify the correct source; the program will not guess.

### 2. Inspect without exporting

```sh
python scripts/sync_to_master_hub.py --db auto --diagnose
```

You can also run an explicit source path:

```sh
python scripts/sync_to_master_hub.py --db "$HOME/apex-revenue-system/garcar_forge_operational.db" --diagnose
```

The SQLite connection is opened `mode=ro` with `query_only`. No SQL writes are issued by the exporter. When another process uses WAL mode, SQLite may still checkpoint or alter auxiliary WAL files; don't infer byte-for-byte immutability from read-only queries.

### 3. Save only a redacted local snapshot inside the actual master hub

The export destination must be a Git checkout of `Garrettc123/systems-master-hub`. `--repo .` while inside Apex is intentionally rejected.

```sh
git clone --depth 1 --single-branch --branch feat/redacted-sqlite-swarm-sync-20261117 \
  https://github.com/Garrettc123/systems-master-hub.git \
  "$HOME/systems-master-hub-sync"

python scripts/sync_to_master_hub.py \
  --db auto \
  --repo "$HOME/systems-master-hub-sync"
```

This writes `telemetry/public_swarm_snapshot.json` to the **local** master-hub clone. It does not push anything to GitHub. The snapshot is aggregated and redacted; nevertheless, inspect it before any public release.

**No auto-push:** Publishing to a public GitHub review branch requires separate explicit `--push --allow-public` opt-in, a clean checkout, an allowlisted remote and an independently approved release. Never commit raw SQLite, WAL, private events, secrets or vulnerability reports.

## Evidence status

This PR contains the utility and synthetic offline tests only. It does not prove that a local `garcar_forge_operational.db` exists, that its schema matches, that live agents are executing, or that any bounty payment was received. The real Termux filesystem is not accessible from GitHub CI.
