#!/usr/bin/env bash
# Clone the four public Kubernetes repositories into data/raw/ (git-ignored). Safe to re-run: existing clones are left alone.
# GIT_CONFIG_GLOBAL=/dev/null bypasses a personal ~/.gitconfig that rewrites https GitHub URLs to SSH (no SSH key is needed here).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw
export GIT_CONFIG_GLOBAL=/dev/null
clone() {  # name [extra git clone args...]
  local name=$1; shift
  if [ -d "data/raw/$name/.git" ]; then echo "skip: data/raw/$name already cloned"; return; fi
  echo "cloning kubernetes/$name ..."
  git -c http.lowSpeedLimit=0 -c http.lowSpeedTime=999999 clone "$@" "https://github.com/kubernetes/$name.git" "data/raw/$name"
}
clone community
clone enhancements
clone website --filter=blob:none        # blobless: full history, old file contents fetched on demand
clone kubernetes --filter=blob:none     # large (~1 GB of history); the ownership history reads old OWNERS files lazily
echo "done. Record the commits with: uv run python -m lineage.ingest.manifest"
