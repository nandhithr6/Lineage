#!/usr/bin/env bash
# Re-read the Kubernetes sources and report what changed since the current snapshot.
#   1. archive a digest of the CURRENT snapshot (snapshot/history/<date>.json) if there is none yet
#   2. pull newer commits of the public repos, rebuild snapshot/ and record the new commits
#   3. reload the graph, re-run the detector, write a digest of the NEW snapshot, print the diff
# NOT run end to end (it changes snapshot/ and invalidates the saved demo answers, which then need `python -m lineage.agent.build_demo_cache`); every step is an existing
# command that was run on its own. Needs the clones from scripts/fetch_data.sh, a running FalkorDB and .env. Review `git diff --stat snapshot/` before committing anything.
set -euo pipefail
cd "$(dirname "$0")/.."
export GIT_CONFIG_GLOBAL=/dev/null
RUN="uv run --env-file .env python"
OLD=$(python3 -c 'import json; print(json.load(open("snapshot/manifest.json"))["snapshot_date"])')
[ -f "snapshot/history/$OLD.json" ] || $RUN -m lineage.changes digest --ownership-sample 138 --out "snapshot/history/$OLD.json"
for r in community enhancements website kubernetes; do git -C "data/raw/$r" pull --ff-only --quiet; done
for m in lineage.ingest.gates lineage.ingest.docs_labels lineage.ingest.keps lineage.ingest.sigs lineage.ingest.owners lineage.link.linker lineage.ingest.manifest; do $RUN -m "$m" > /dev/null; done
$RUN -m lineage.graph.loader > /dev/null
$RUN -m lineage.detect.drift --write > /dev/null
NEW=$(python3 -c 'import json; print(json.load(open("snapshot/manifest.json"))["snapshot_date"])')
$RUN -m lineage.changes digest --ownership-sample 138 --out "snapshot/history/$NEW.json"
if [ "$OLD" = "$NEW" ]; then echo "Same snapshot date ($NEW): the digest was overwritten; compare with an older one."; fi
$RUN -m lineage.changes diff "snapshot/history/$OLD.json" "snapshot/history/$NEW.json" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["summary"], indent=1))'
