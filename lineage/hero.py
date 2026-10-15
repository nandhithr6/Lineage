"""Hero workflow in the terminal: Find -> Trace -> Explain -> Repair (the repair draft arrives in Step 9).

Run: `uv run --env-file .env python -m lineage.hero resource-quotas`
Every fact is followed by its source link [eN]; where evidence is missing the output says "insufficient evidence".
"""
from __future__ import annotations

import argparse
import textwrap

from lineage.config import load_settings
from lineage.graph.client import connect
from lineage.graph.trace import build_trace


def src(bundle: dict, eid: str | None) -> str:
    e = next((x for x in bundle["evidence"] if x["id"] == eid), None)
    return f"\n      source [{eid}]: {e['source_url']}" if e and e["source_url"] else ""


def render(b: dict) -> str:
    out: list[str] = []
    w = out.append
    s = b["sections"]
    if b["status"] != "ok":
        return f"INSUFFICIENT EVIDENCE: {s['problem']}\n(no claim, gate or finding to trace; nothing is guessed)"
    f, c, link, gate, kep = s["finding"], s["claim"], s["link"], s["gate"], s["kep"]
    w("=" * 100)
    w(f"LINEAGE  |  {s['page']['title']}  ({s['page']['url']})")
    w("=" * 100)
    w(f"\n1. FIND  [{f['severity'].upper()}: {f['kind']}]\n   {f['message']}")
    w(f"\n2. THE CLAIM\n   section: \"{c['heading']}\"   (line {c['line']})\n   label:   {c['shortcode']}   ->  says {c['stage']}"
      f"{' since v' + c['since'] if c['since'] else ''}{src(b, c['evidence_id'])}")
    w("\n3. LINKED TO FEATURE GATE")
    if link:
        how = ("verified by hand by " + str(link["verified_by"]) + " on " + str(link["verified_on"]) if link["method"] == "manual"
               else "inferred by the linker")
        w(f"   {gate['name']}   confidence {link['link_confidence']} ({link['method']}; {how})")
        if link["method"] == "manual":
            lc = link["linker_confidence"]
            if lc is not None and lc >= 0.8:
                w(f"   the automatic linker independently chose the same gate on its own (confidence {lc}); the hand verification is kept as an audit backup")
            else:
                w(f"   linker alone scored {lc}; reason for the hand verification: {textwrap.shorten(str(link['reason']), 230)}")
        elif link["signals"]:
            w("   evidence: " + ", ".join(f"{k}={v}" for k, v in link["signals"].items() if not isinstance(v, list)))
        for e in link.get("eliminated") or []:
            w(f"   ruled out: {e['reason']}")
        w(src(b, link["evidence_id"]).lstrip("\n"))
    else:
        w("   insufficient evidence: no ABOUT link recorded for this claim")
    w(f"\n4. WHAT THE GATE HISTORY SAYS  (gate {'removed' if gate['removed'] else 'active'})")
    for h, eid in zip(gate["history"], gate["history_evidence"]):
        span = h["from"] if h["from"] == h["to"] else f"{h['from']} to {h['to']}"
        w(f"   {h['stage']:<10} {span:<14}{'  (overlap in source)' if h['overlap'] else ''}   [{eid}]")
    w(f"   source: {gate['source_url']}")
    w(f"\n5. EXPLAIN\n   claimed : {c['stage']}{' since v' + c['since'] if c['since'] else ''}\n   verified: {f['message'].split(', but ')[-1]}")
    w("\n6. THE DECISION BEHIND IT")
    if kep:
        w(f"   KEP {kep['id']}  \"{kep['title']}\"\n   status {kep['status']}, stage {kep['stage']}, milestone {kep['milestone']}; "
          f"link to the gate: {kep['method']} (confidence {kep['confidence']}){src(b, kep['evidence_id'])}")
        if kep["sig"]:
            w(f"   owned by team (SIG): {kep['sig']} - {kep['sig_name']}{src(b, kep['sig_evidence_id'])}")
        if kep["others"]:
            w("   other KEPs that mention the gate: " + ", ".join(f"{k['id']} ({k['method']})" for k in kep["others"][:4]))
    else:
        w("   insufficient evidence: no KEP linked to this gate")
    w("\n7. SUGGESTED REVIEWERS  (emeritus approvers are never suggested; a person scores once per kind of role)")
    if s["reviewers"]:
        for group, why in (("docs approver", "can merge the doc fix"), ("domain expert", "can check the facts")):
            members = [r for r in s["reviewers"] if r["group"] == group]
            if members:
                w(f"   -- {group}s ({why})")
            for r in members:
                w(f"   @{r['handle']:<18} score {r['score']}  [{r['evidence_id']}]")
                for x in r["reasons"][:3]:
                    w(f"        - {x['reason']}")
    else:
        w("   insufficient evidence: no owners found")
    w("\n8. GRAPH PATH\n   " + "\n     -> ".join(b["path"]))
    w("\n9. REPAIR\n   (draft fix arrives in Step 9; Lineage never posts to Kubernetes)")
    w("\nEVIDENCE INDEX")
    for e in b["evidence"]:
        w(f"   [{e['id']}] {e['type']:<10} {e['source_url'] or '(derived from the graph)'}")
    if s["other_matches"]:
        w(f"\n({len(s['other_matches'])} other matching finding(s)/review items on pages containing this text; pass a more specific query)")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Trace one drift finding end to end")
    ap.add_argument("query", help="part of the docs page path, e.g. resource-quotas")
    ap.add_argument("--graph", default=None)
    args = ap.parse_args()
    s = load_settings()
    g = connect(s).select_graph(args.graph or s.falkordb_graph)
    print(render(build_trace(g, args.query)))


if __name__ == "__main__":
    main()
