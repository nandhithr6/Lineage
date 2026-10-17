"""Precomputed ownership views (orphaned, dormant, gaps) for the Insights page.

Computing them walks every folder with listed approvers and joins it with the review history: about 10 s on a laptop and 90 s on a free web host with a fraction of a CPU, which
made the first visit to Insights hang. The result depends only on the committed snapshot, so it is computed once from the loaded graph and stored in snapshot/ownership_views.json
together with a fingerprint of the snapshot files it was computed from; the API serves the file while the fingerprint matches and falls back to computing live otherwise.
The activity window ends at the SNAPSHOT date (the PR data ends there), not at today's date, so the page is deterministic and honest about what it covers.

    uv run --env-file .env python -m lineage.graph.ownership_views        # rewrite snapshot/ownership_views.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from falkordb import Graph

from lineage.graph.queries import dormant_components, orphaned_components, ownership_gaps, scan_ownership

log = logging.getLogger(__name__)
SNAP = Path("snapshot")
VIEWS_PATH = SNAP / "ownership_views.json"
MONTHS = 12
INPUTS = ("prs.jsonl", "owners_edges.jsonl", "codepaths.jsonl", "team_members.jsonl", "sigs.jsonl")


def fingerprint(snap: Path = SNAP) -> str:
    h = hashlib.sha256(f"months{MONTHS}".encode())
    for name in INPUTS:
        f = snap / name
        h.update(name.encode())
        h.update(hashlib.sha256(f.read_bytes()).digest() if f.exists() else b"missing")
    return h.hexdigest()[:16]


def snapshot_today(snap: Path = SNAP) -> datetime:
    """The end of the activity window: the snapshot date (midnight UTC)."""
    manifest = json.loads((snap / "manifest.json").read_text())
    return datetime.fromisoformat(manifest["snapshot_date"]).replace(tzinfo=timezone.utc)


def compute(g: Graph, months: int = MONTHS, today: datetime | None = None) -> dict:
    today = today or snapshot_today()
    scan = scan_ownership(g, months, today)
    return {"orphans": orphaned_components(g, months, today, scan=scan), "dormant": dormant_components(g, months, today, scan=scan), "gaps": ownership_gaps(g, months, today, limit=12, scan=scan)}


def load_precomputed(months: int, path: Path = VIEWS_PATH, snap: Path = SNAP) -> dict | None:
    """The stored views if they were computed for this exact snapshot and window, else None."""
    if months != MONTHS or not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return None
    return data["views"] if data.get("fingerprint") == fingerprint(snap) and data.get("months") == months else None


def main() -> None:
    from lineage.config import load_settings
    from lineage.graph.client import connect

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    s = load_settings()
    g = connect(s).select_graph(s.falkordb_graph)
    views = compute(g)
    VIEWS_PATH.write_text(json.dumps({"fingerprint": fingerprint(), "months": MONTHS, "as_of": snapshot_today().date().isoformat(), "views": views}, sort_keys=True, default=str))
    log.info("wrote %s: %d orphaned, %d dormant, %d gaps (window ends %s)", VIEWS_PATH, len(views["orphans"]), len(views["dormant"]), len(views["gaps"]), snapshot_today().date())


if __name__ == "__main__":
    main()
