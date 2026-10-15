"""Write snapshot/manifest.json: which commits of which repos the snapshot was built from, when, and what it contains.

Run: `uv run python -m lineage.ingest.manifest` (after the ingest steps). /about-data serves this file.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from lineage.ingest.gates import repo_sha

log = logging.getLogger(__name__)
SNAP = Path("snapshot")
REPOS = {
    "kubernetes/website": ("data/raw/website", "CC BY 4.0"),
    "kubernetes/enhancements": ("data/raw/enhancements", "Apache-2.0"),
    "kubernetes/community": ("data/raw/community", "Apache-2.0"),
    "kubernetes/kubernetes": ("data/raw/kubernetes", "Apache-2.0"),
}


def line_count(p: Path) -> int:
    return sum(1 for line in p.read_text().splitlines() if line.strip()) if p.exists() else 0


def build() -> dict:
    return {
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "sources": [{"repo": name, "commit": repo_sha(Path(path)), "license": lic, "url": f"https://github.com/{name}"}
                    for name, (path, lic) in REPOS.items()],
        "files": {p.name: line_count(p) for p in sorted(SNAP.glob("*.jsonl"))},
        "owners_history": "kubernetes/kubernetes sampled at tags v1.30.0..v1.37.0 and HEAD (1.38-dev); kubernetes/website at HEAD only",
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    m = build()
    (SNAP / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    log.info("manifest written: %d repos, %d files", len(m["sources"]), len(m["files"]))


if __name__ == "__main__":
    main()
