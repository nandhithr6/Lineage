#!/usr/bin/env bash
# Build the DIGEST of the Kubernetes sources as they were on a past date, by running the same pipeline on the repos at that date.
# Usage: scripts/build_previous_snapshot.sh 2026-07-16        -> snapshot/history/2026-07-16.json (commit it; /changes-since compares against it)
# Needs data/raw/{website,enhancements,community} (scripts/fetch_data.sh), a running FalkorDB and .env. Uses a scratch graph (lineage_prev) that is deleted at the end.
# Scope: docs labels, gates, KEPs, org chart, links and findings are rebuilt at that date; ownership comes from the current OWNERS history, read at the release that was
# current on that date (it is sampled at release tags anyway).
set -euo pipefail
DATE=${1:?usage: build_previous_snapshot.sh YYYY-MM-DD}
cd "$(dirname "$0")/.."
ROOT=$PWD; W="$ROOT/data/prev_$DATE"; PY="$ROOT/.venv/bin/python"
export GIT_CONFIG_GLOBAL=/dev/null PYTHONPATH="$ROOT"
mkdir -p "$W/data/raw" "$W/snapshot"

add_tree() {  # repo [--sparse]
  local r=$1 sha
  sha=$(git -C "data/raw/$r" rev-list -1 --before="$DATE 23:59" HEAD)
  if [ ! -e "$W/data/raw/$r/.git" ]; then
    git -C "data/raw/$r" worktree add --no-checkout --detach "$W/data/raw/$r" "$sha" >/dev/null
    if [ "${2:-}" = "--sparse" ]; then git -C "$W/data/raw/$r" sparse-checkout set content/en/docs; fi
    git -C "$W/data/raw/$r" checkout >/dev/null 2>&1
  fi
}
add_tree enhancements
add_tree community
add_tree website --sparse

for f in codepaths owners_edges team_members release_dates; do cp "snapshot/$f.jsonl" "$W/snapshot/"; done
: > "$W/snapshot/verified_links.jsonl"
(cd "$W" && for m in lineage.ingest.gates lineage.ingest.docs_labels lineage.ingest.keps lineage.ingest.sigs lineage.link.linker; do "$PY" -m "$m" >/dev/null 2>&1; done)

"$PY" -c '
import json, subprocess, sys
date, w = sys.argv[1], sys.argv[2]
sha = lambda r: subprocess.run(["git", "-C", w + "/data/raw/" + r, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
json.dump({"snapshot_date": date, "sources": [{"repo": "kubernetes/" + r, "commit": sha(r)} for r in ("website", "enhancements", "community")]}, open(w + "/snapshot/manifest.json", "w"))
' "$DATE" "$W"

set -a; . ./.env; set +a
export FALKORDB_GRAPH=lineage_prev
(cd "$W" && "$PY" -m lineage.graph.loader >/dev/null 2>&1 && "$PY" -m lineage.detect.drift --write >/dev/null 2>&1)
"$PY" -m lineage.changes digest --snapshot-dir "$W/snapshot" --out "snapshot/history/$DATE.json"
"$PY" -c '
from lineage.config import load_settings
from lineage.graph.client import connect
s = load_settings()
connect(s).select_graph("lineage_prev").delete()
'
echo "done: snapshot/history/$DATE.json (the workspace $W can be deleted; then run: git -C data/raw/<repo> worktree prune)"
