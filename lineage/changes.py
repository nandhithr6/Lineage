"""Keeping knowledge current: what changed between two snapshots of the Kubernetes sources.

A DIGEST is a small JSON summary of one snapshot plus the findings the detector produced from it (findings, hard-coded labels, gate stage history, KEP status, who
holds which ownership role at the latest sampled release). `diff_digests(previous, current)` compares two digests and answers the question that matters for a company
brain: which stale docs got fixed (resolved), which went stale (new), which features changed stage, which ownership roles moved.

Digests of earlier snapshots live in snapshot/history/<date>.json. Each is made by running the SAME pipeline (ingest -> linker -> loader -> detector) on the repos as they
were at that date (scripts/build_previous_snapshot.sh), so a "resolved" finding means: the detector found it then, does not find it now, and the diff says why.

    uv run --env-file .env python -m lineage.changes digest --out snapshot/history/2026-10-15.json     # digest of the live snapshot + graph
    uv run python -m lineage.changes diff snapshot/history/2026-07-16.json snapshot/history/2026-10-15.json
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)
HISTORY_DIR = Path("snapshot/history")
EXAMPLES = 40  # cap on listed examples per ownership bucket

Q_FINDINGS = """
MATCH (f:Finding {active: true})-[:FOR_CLAIM]->(c:Claim)<-[:HAS_CLAIM]-(s:DocSection)<-[:HAS_SECTION]-(p:DocPage), (f)-[:FOR_GATE]->(g:FeatureGate)
RETURN f.key, f.kind, f.severity, f.message, f.claimed_stage, f.claimed_since, g.name, c.id, p.title, p.url, s.heading, f.anchored_by, f.detected_at
"""


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def sample_for_date(release_dates: list[dict], as_of_date: str, latest: int | None = None) -> int:
    """Ownership is sampled at release tags: the newest sampled release whose date is on or before `as_of_date` (or `latest` when given)."""
    if latest is not None:
        return latest
    done = [r["num"] for r in release_dates if r["date"][:10] <= as_of_date]
    return max(done) if done else min(r["num"] for r in release_dates)


def ownership_at(edges: list[dict], sample: int) -> list[list[str]]:
    """Role edges that hold at a sampled release: valid_from <= sample and (valid_to is null or valid_to > sample)."""
    return sorted([e["repo"], e["dir"], e["role"], e["subject"]] for e in edges
                  if e["valid_from"] <= sample and (e["valid_to"] is None or e["valid_to"] > sample))


def build_digest(snapshot_dir: Path, findings: list[dict], date: str, sources: list[dict], ownership_sample: int | None = None) -> dict:
    labels = [x for x in read_jsonl(snapshot_dir / "doc_labels.jsonl") if x["kind"] == "hardcoded"]
    sample = sample_for_date(read_jsonl(snapshot_dir / "release_dates.jsonl"), date, ownership_sample)
    return {
        "snapshot_date": date,
        "sources": sources,
        "ownership_sample": sample,
        "findings": {f["key"]: f for f in findings},
        "claims": {x["id"]: {"state": x["state"], "since": x["for_k8s_version"], "page": x["page_path"], "heading": x["heading"]} for x in labels},
        "gates": {g["name"]: {"removed": g["removed"], "stages": [[s["stage"], s["from_version"], s["to_version"]] for s in g["stages"]]}
                  for g in read_jsonl(snapshot_dir / "gates.jsonl")},
        "keps": {k["id"]: {"status": k["status"], "stage": k["stage"], "milestone": k["latest_milestone"]} for k in read_jsonl(snapshot_dir / "keps.jsonl")},
        "ownership": ownership_at(read_jsonl(snapshot_dir / "owners_edges.jsonl"), sample),
    }


def findings_from_graph(g) -> list[dict]:
    keys = ["key", "kind", "severity", "message", "claimed_stage", "claimed_since", "gate", "claim_id", "page_title", "page_url", "section", "anchored_by", "detected_at"]
    return [dict(zip(keys, row)) for row in g.ro_query(Q_FINDINGS).result_set]


def _last_stage(gate: dict) -> str | None:
    return gate["stages"][-1][0] if gate["stages"] else None


def _describe_gate_change(old: dict, new: dict) -> str:
    """e.g. "beta -> stable (now stable since v1.37)", plus ", gate removed" when the gate file now says removed."""
    last = new["stages"][-1] if new["stages"] else None
    since = f" (now {last[0]} since v{last[1]})" if last else ""
    stage_part = f"{_last_stage(old) or 'none'} -> {_last_stage(new) or 'none'}{since}" if _last_stage(old) != _last_stage(new) else f"history edited{since}"
    return stage_part + (", gate removed" if new["removed"] and not old["removed"] else "")


def why_resolved(f: dict, prev: dict, cur: dict) -> str:
    cid = f["claim_id"]
    if cid not in cur["claims"]:
        return "the label or its section no longer exists in the docs (removed or rewritten)"
    was, now = prev["claims"].get(cid), cur["claims"][cid]
    if was and (was["state"], was["since"]) != (now["state"], now["since"]):
        return f"the docs label changed from {was['state']} since v{was['since']} to {now['state']} since v{now['since']}: fixed upstream"
    return "no longer detected although the label is unchanged (the link, the gate history or a detector rule changed)"


def why_new(f: dict, prev: dict, cur: dict) -> str:
    cid, gate = f["claim_id"], f["gate"]
    if cid not in prev["claims"]:
        return "the label is new in the docs since the earlier snapshot"
    if gate in prev["gates"] and gate in cur["gates"] and prev["gates"][gate]["stages"] != cur["gates"][gate]["stages"]:
        return f"the gate changed ({_describe_gate_change(prev['gates'][gate], cur['gates'][gate])}) while the docs label stayed the same"
    return "newly detected although label and gate are unchanged (the link or a detector rule changed)"


def diff_digests(prev: dict, cur: dict) -> dict:
    pf, cf = prev["findings"], cur["findings"]
    new = [{**cf[k], "why": why_new(cf[k], prev, cur)} for k in sorted(set(cf) - set(pf))]
    resolved = [{**pf[k], "why": why_resolved(pf[k], prev, cur)} for k in sorted(set(pf) - set(cf))]
    # A section that moved to another page shows up as one resolved + one new finding with the same gate and the same label text: that is NOT a fix.
    moved = []
    for r in list(resolved):
        twin = next((n for n in new if n["gate"] == r["gate"] and (n["claimed_stage"], n["claimed_since"]) == (r["claimed_stage"], r["claimed_since"])
                     and n["claim_id"] not in prev["claims"] and r["claim_id"] not in cur["claims"]), None)
        if twin:
            resolved.remove(r)
            new.remove(twin)
            moved.append({"gate": r["gate"], "label": f"{r['claimed_stage']} since v{r['claimed_since']}", "from_page": r["page_title"], "to_page": twin["page_title"],
                          "kind": twin["kind"], "severity": twin["severity"], "why": "the section moved to another page; the label and the finding are unchanged (not a fix)"})
    persisting = [cf[k] for k in sorted(set(cf) & set(pf))]

    stages = []
    for name in sorted(set(prev["gates"]) | set(cur["gates"])):
        a, b = prev["gates"].get(name), cur["gates"].get(name)
        if a is None:
            stages.append({"gate": name, "change": "new gate", "now": _last_stage(b)})
        elif b is None:
            stages.append({"gate": name, "change": "gate file removed from the docs"})
        elif a["stages"] != b["stages"] or a["removed"] != b["removed"]:
            stages.append({"gate": name, "change": _describe_gate_change(a, b), "now": _last_stage(b)})

    labels = []
    for cid in sorted(set(prev["claims"]) | set(cur["claims"])):
        a, b = prev["claims"].get(cid), cur["claims"].get(cid)
        if a is None:
            labels.append({"claim": cid, "change": "added", "now": f"{b['state']} since v{b['since']}"})
        elif b is None:
            labels.append({"claim": cid, "change": "removed", "was": f"{a['state']} since v{a['since']}"})
        elif (a["state"], a["since"]) != (b["state"], b["since"]):
            labels.append({"claim": cid, "change": "edited", "was": f"{a['state']} since v{a['since']}", "now": f"{b['state']} since v{b['since']}"})

    keps = [{"kep": k, "was": prev["keps"][k]["status"], "now": cur["keps"][k]["status"]} for k in sorted(set(prev["keps"]) & set(cur["keps"]))
            if prev["keps"][k]["status"] != cur["keps"][k]["status"]]
    new_keps = sorted(set(cur["keps"]) - set(prev["keps"]))

    po, co = {tuple(x) for x in prev["ownership"]}, {tuple(x) for x in cur["ownership"]}
    gained, lost = sorted(co - po), sorted(po - co)
    findings_resolved = [r for r in resolved if r["severity"] == "finding"]
    findings_new = [n for n in new if n["severity"] == "finding"]
    return {
        "from": {"snapshot_date": prev["snapshot_date"], "sources": prev["sources"], "ownership_sample": prev["ownership_sample"]},
        "to": {"snapshot_date": cur["snapshot_date"], "sources": cur["sources"], "ownership_sample": cur["ownership_sample"]},
        "summary": {"findings_new": len(findings_new), "findings_resolved": len(findings_resolved), "findings_persisting": len([f for f in persisting if f["severity"] == "finding"]) + len([m for m in moved if m["severity"] == "finding"]),
                    "findings_moved": len([m for m in moved if m["severity"] == "finding"]),
                    "review_items_new": len(new) - len(findings_new), "review_items_resolved": len(resolved) - len(findings_resolved),
                    "gates_changed": len(stages), "labels_changed": len(labels), "keps_status_changed": len(keps), "keps_new": len(new_keps),
                    "ownership_gained": len(gained), "ownership_lost": len(lost)},
        "findings": {"new": new, "resolved": resolved, "moved": moved, "persisting": persisting},
        "gates": stages,
        "labels": labels,
        "keps": {"status_changed": keps, "new": new_keps},
        "ownership": {"gained": [dict(zip(("repo", "path", "role", "who"), x)) for x in gained[:EXAMPLES]],
                      "lost": [dict(zip(("repo", "path", "role", "who"), x)) for x in lost[:EXAMPLES]],
                      "note": f"Ownership is sampled at release tags: compares the roles held at release 1.{str(prev['ownership_sample'])[1:]} with those at "
                              f"{'HEAD (1.' + str(cur['ownership_sample'])[1:] + '-dev)' if cur['ownership_sample'] == 138 else 'release 1.' + str(cur['ownership_sample'])[1:]}."},
    }


def load_history(directory: Path = HISTORY_DIR) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))} if directory.exists() else {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digest", help="write a digest of snapshot/ and the findings in the graph")
    d.add_argument("--snapshot-dir", default="snapshot")
    d.add_argument("--out", required=True)
    d.add_argument("--date", default=None, help="snapshot date (default: from the manifest)")
    d.add_argument("--ownership-sample", type=int, default=None, help="sampled release to read ownership at (138 = HEAD, for the live snapshot); default: newest release on or before the date")
    d.add_argument("--sources-from", default=None, help="a manifest.json to take the repo commits from (default: <snapshot-dir>/manifest.json)")
    x = sub.add_parser("diff", help="print the diff of two digest files")
    x.add_argument("previous")
    x.add_argument("current")
    args = ap.parse_args()
    if args.cmd == "digest":
        from lineage.config import load_settings
        from lineage.graph.client import connect

        s = load_settings()
        g = connect(s).select_graph(s.falkordb_graph)
        snap = Path(args.snapshot_dir)
        manifest = json.loads(Path(args.sources_from or snap / "manifest.json").read_text())
        out = build_digest(snap, findings_from_graph(g), args.date or manifest["snapshot_date"], [{"repo": r["repo"], "commit": r["commit"]} for r in manifest["sources"]], args.ownership_sample)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, sort_keys=True, separators=(",", ":")))
        log.info("digest %s: %d findings, %d claims, %d gates, %d ownership roles -> %s", out["snapshot_date"], len(out["findings"]), len(out["claims"]), len(out["gates"]), len(out["ownership"]), args.out)
    else:
        print(json.dumps(diff_digests(json.loads(Path(args.previous).read_text()), json.loads(Path(args.current).read_text())), indent=1))


if __name__ == "__main__":
    main()
