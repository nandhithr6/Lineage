"""Build the full evidence trace for one finding: claim -> link -> gate history -> KEP -> SIG -> owners.

Returns an evidence bundle (CLAUDE.md §8): {tool, params, evidence[], path[], status, sections}.
Every fact comes from a graph query and carries its source_url; the CLI (lineage.hero) and the agent's
`explain_finding` tool both render from this one function so they cannot disagree.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from collections import defaultdict

from falkordb import Graph

from lineage.graph.queries import LATEST, effective_owners, experts_for, network_ranks, official_vs_actual

Q_FINDINGS = """
MATCH (f:Finding {active: true})-[:FOR_CLAIM]->(c:Claim)<-[:HAS_CLAIM]-(s:DocSection)<-[:HAS_SECTION]-(p:DocPage)
WHERE ($q IS NULL OR p.path CONTAINS $q OR f.key = $q)
RETURN f.key, f.kind, f.severity, f.message, f.claimed_stage, f.claimed_since, f.actual_stages, f.entered_version,
       f.gate_removed, c.id, c.shortcode, c.line, c.source_url, s.heading, p.path, p.url, p.title
"""
NETWORK_TOP = 40  # a person counts as central when they are in the top 40 of the review network
PR_STRONG = 0.70  # PR -> KEP links at or above this count as 'implementing' (lineage/link/pr_kep.py)
SEVERITY_ORDER = {"finding": 0, "minor": 1, "review": 2}

Q_FINDING_GATE = "MATCH (f:Finding {key: $k})-[:FOR_GATE]->(g:FeatureGate), (f)-[:AS_OF]->(r:Release) RETURN g.name, g.removed, g.source_url, r.version"
Q_LINK = """
MATCH (c:Claim {id: $c})-[a:ABOUT]->(g:FeatureGate {name: $g})
RETURN a.method, a.link_confidence, a.linker_confidence, a.signals, a.verified_by, a.verified_on, a.reason, a.source_url, a.eliminated
"""
Q_HISTORY = "MATCH (g:FeatureGate {name: $g})-[e:STAGE_IN]->(r:Release) RETURN r.num, r.version, e.stage, e.overlap, e.source_url ORDER BY r.num"
Q_KEPS = """
MATCH (k:KEP)-[i:INTRODUCES]->(g:FeatureGate {name: $g})
OPTIONAL MATCH (k)-[:OWNED_BY]->(s:SIG)
RETURN k.id, k.title, k.status, k.stage, k.latest_milestone, k.source_url, i.method, i.confidence, i.source_url, s.id, s.name, s.source_url
"""
Q_KEP_PRS = """
MATCH (p:PR)-[e:IMPLEMENTS]->(k:KEP {id: $k}) WHERE e.confidence >= $min
OPTIONAL MATCH (p)-[f:FIXES]->(i:Issue) WHERE ID(f) >= 0
RETURN p.number, p.title, p.merged_at, p.url, e.method, e.confidence, e.evidence, collect(DISTINCT [i.id, i.title, i.state, i.url])
ORDER BY e.confidence DESC, p.merged_at DESC LIMIT $limit
"""
Q_KEP_PR_COUNTS = "MATCH (p:PR)-[e:IMPLEMENTS]->(k:KEP {id: $k}) RETURN sum(CASE WHEN e.confidence >= $min THEN 1 ELSE 0 END), sum(CASE WHEN e.confidence < $min THEN 1 ELSE 0 END)"
Q_KEP_APPROVERS = "MATCH (p:Person)-[:APPROVED_KEP]->(k:KEP {id: $k}) RETURN p.handle"
Q_SIG_LEADERS = "MATCH (p:Person)-[r:CHAIRS|TECH_LEAD_OF]->(s:SIG {id: $s}) RETURN p.handle, type(r), r.source_url"
Q_SIG_PATHS = "MATCH (s:SIG {id: $s})-[:OWNS]->(c:CodePath) RETURN c.id, c.path ORDER BY c.path"


def stage_ranges(rows: list[list]) -> list[dict]:
    """Collapse per-release rows into runs of the same stage: [{stage, from, to, overlap, source_url}]."""
    runs: list[dict] = []
    for _, version, stage, overlap, url in rows:
        if runs and runs[-1]["stage"] == stage and not overlap:
            runs[-1]["to"] = version
        else:
            runs.append({"stage": stage, "from": version, "to": version, "overlap": bool(overlap), "source_url": url})
    return runs


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|\d+", text)}


def rank_keps(rows: list[list], gate: str) -> list[dict]:
    """Order the KEPs that introduce a gate: kep.yaml beats README mention, then confidence, then title/gate-name overlap."""
    gw = _words(gate)
    keps = []
    for kid, title, status, stage, milestone, kurl, method, conf, iurl, sig_id, sig_name, sig_url in rows:
        overlap = len(gw & _words(title)) / max(1, len(gw | _words(title)))
        keps.append({"id": kid, "title": title, "status": status, "stage": stage, "milestone": milestone, "source_url": kurl,
                     "method": method, "confidence": conf, "introduces_url": iurl, "sig": sig_id, "sig_name": sig_name,
                     "sig_url": sig_url, "name_overlap": round(overlap, 2)})
    return sorted(keps, key=lambda k: (k["method"] != "kep_yaml", -k["confidence"], -k["name_overlap"], k["id"]))


def _short(items: list[str], n: int = 3) -> str:
    items = sorted(set(items))
    return ", ".join(items[:n]) + (f" (+{len(items) - n} more)" if len(items) > n else "")


_CONNECTIONS: dict[tuple, tuple[float, dict | None]] = {}


def connection_to(g: Graph, handle: str, gate: str, ttl: int = 600) -> dict | None:
    """How a person connects to a feature gate: the cheapest structural path (FalkorDB algo.SPpaths), cached for a few minutes. None when there is none within 4 hops."""
    import time

    from lineage.graph.paths import PathTimeout, shortest_path

    key = (g.name, handle, gate)
    hit = _CONNECTIONS.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        p = shortest_path(g, ("Person", handle), ("FeatureGate", gate))
    except PathTimeout:
        p = None
    _CONNECTIONS[key] = (time.time(), p)
    return p


CONNECTION_PEOPLE = 6  # path search is run for the best-scoring people only


def suggest_reviewers(g: Graph, page_path: str, sig_id: str | None, kep_id: str | None, as_of: int = LATEST, today: datetime | None = None, gate: str | None = None) -> list[dict]:
    """Docs approvers + feature-team code approvers + design-decision approvers + team leads, each with reasons.

    A person scores once per CATEGORY (docs approver 1.0, code approver 1.0, KEP approver 0.8, SIG lead 0.5), so holding the
    same role through several paths or teams does not inflate the ranking. Emeritus approvers are never suggested.
    """
    WEIGHT = {"docs": 1.0, "code": 1.0, "kep": 0.8, "active": 0.7, "lead": 0.5, "network": 0.3}
    cats: dict[str, dict[str, dict]] = defaultdict(dict)  # handle -> category -> {paths, teams, url}

    def add(handle: str, cat: str, path: str | None, team: str | None, url: str | None) -> None:
        e = cats[handle].setdefault(cat, {"paths": [], "teams": [], "url": url})
        if path is not None:
            e["paths"].append(path or "/")
        if team:
            e["teams"].append(team)

    for r in effective_owners(g, "website", page_path, as_of, is_file=True):
        if r["role"] == "APPROVER_OF":
            add(r["handle"], "docs", r["declared_at"], r["via_team"], r["source_url"])
    if sig_id:
        for _, path in g.ro_query(Q_SIG_PATHS, {"s": sig_id}).result_set:
            for r in effective_owners(g, "kubernetes", path, as_of):
                if r["role"] == "APPROVER_OF" and r["declared_at"] == path:
                    add(r["handle"], "code", path, r["via_team"], r["source_url"])
        # Real activity (PR data, when loaded): people who recently review under the team's code paths; listed approvers who have gone quiet are dropped.
        inactive: set[str] = set()
        for _, path in g.ro_query(Q_SIG_PATHS, {"s": sig_id}).result_set:
            ex = experts_for(g, "kubernetes", path, today=today, limit=8)
            if not ex:
                continue
            v = official_vs_actual(g, "kubernetes", path, today=today)
            inactive |= set(v["inactive_approvers"]) - set(v["active_approvers"])
            now = today or datetime.now(timezone.utc)
            for e in ex:
                if (now.date() - datetime.fromisoformat(e["last_active"]).date()).days > 365:
                    continue  # reviewed here once, long ago: not "active"
                add(e["handle"], "active", f"{e['prs_reviewed']} recent PRs under {path} (last {e['last_active']})", None, e["examples"][0]["url"])
        for h in inactive:
            if h in cats and "active" not in cats[h]:
                cats[h].pop("code", None)  # listed in OWNERS but no activity in 12 months: not suggested as a domain expert
                if not cats[h]:
                    del cats[h]
        for handle, rel, url in g.ro_query(Q_SIG_LEADERS, {"s": sig_id}).result_set:
            add(handle, "lead", None, None, url)
            cats[handle]["lead"]["role"] = "chair" if rel == "CHAIRS" else "tech lead"
    if kep_id:
        for (handle,) in g.ro_query(Q_KEP_APPROVERS, {"k": kep_id}).result_set:
            add(handle, "kep", None, None, None)
    # Centrality in the review network (FalkorDB PageRank) supports people ALREADY suggested for another reason; it never adds anyone by itself.
    ranks = network_ranks(g) if cats else {}
    for handle in cats:
        r = ranks.get(handle)
        if r and r["rank"] <= NETWORK_TOP:
            ex = g.ro_query("MATCH (:Person {handle: $h})-[:REVIEWED]->(pr:PR) RETURN pr.url ORDER BY pr.merged_at DESC LIMIT 1", {"h": handle}).result_set
            cats[handle]["network"] = {"paths": [], "teams": [], "url": ex[0][0] if ex else None, "rank": r}
    out = []
    for handle, by_cat in cats.items():
        reasons = []
        for cat, e in sorted(by_cat.items(), key=lambda kv: -WEIGHT[kv[0]]):
            via = f" via {_short(e['teams'])}" if e["teams"] else ""
            text = {"docs": f"approves docs under {_short(e['paths'])}{via}",
                    "code": f"approves code in {_short(e['paths'], 2)} ({sig_id}){via}",
                    "kep": f"approved the design decision {kep_id.split('/')[1] if kep_id else ''}",
                    "active": f"actively reviews: {_short(e['paths'], 2)}",
                    "lead": f"{e.get('role', 'lead')} of {sig_id}",
                    "network": (f"central in the review network: #{e['rank']['rank']} of {e['rank']['of']:,} people by PageRank, {e['rank']['authors_served']} different authors rely on their reviews "
                                "(a position in the network, not a measure of skill)") if "rank" in e else ""}[cat]
            reasons.append({"reason": text, "source_url": e["url"], "category": cat})
        out.append({"handle": handle, "score": round(sum(WEIGHT[c] for c in by_cat), 2), "reasons": reasons})
    ranked = sorted(out, key=lambda p: (-p["score"], p["handle"]))
    if gate:  # how each of the best candidates connects to the finding's feature gate: a path computed in FalkorDB, with every hop's source (score unchanged)
        from lineage.graph.paths import describe_path

        for person in [p for p in ranked if any(r["category"] != "docs" for r in p["reasons"])][:CONNECTION_PEOPLE]:
            conn = connection_to(g, person["handle"], gate)
            if conn:
                person["reasons"].append({"reason": f"connects to {gate}: {describe_path(conn)}", "source_url": next((h["source_url"] for h in conn["hops"] if h["source_url"]), None),
                                          "category": "connection", "hops": conn["hops"]})
    return ranked


def build_trace(g: Graph, query: str | None, finding_key: str | None = None) -> dict:
    """Trace the most severe active finding whose page path contains `query` (or whose key equals finding_key)."""
    bundle: dict = {"tool": "explain_finding", "params": {"query": query, "finding_key": finding_key},
                    "evidence": [], "path": [], "status": "ok", "sections": {}}
    ev = bundle["evidence"]

    def add(kind: str, type_: str, frm: str, to: str | None, props: dict, url: str | None) -> str:
        eid = f"e{len(ev) + 1}"
        ev.append({"id": eid, "kind": kind, "type": type_, "from": frm, "to": to, "props": props, "source_url": url})
        return eid

    rows = g.ro_query(Q_FINDINGS, {"q": finding_key or query}).result_set
    if not rows:
        bundle["status"] = "insufficient_evidence"
        bundle["sections"]["problem"] = f"No active finding or review item matches {finding_key or query!r}."
        return bundle
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r[2], 9), r[14], r[9]))
    (key, kind, severity, message, c_stage, c_since, actual, entered, removed, claim_id, shortcode, line, claim_url,
     heading, page_path, page_url, page_title) = rows[0]
    bundle["sections"]["other_matches"] = [{"key": r[0], "kind": r[1], "severity": r[2], "claim": r[9]} for r in rows[1:]]

    claim_eid = add("node", "Claim", f"DocPage:{page_path}", f"Claim:{claim_id}",
                    {"shortcode": shortcode, "stage": c_stage, "since": c_since, "section": heading, "line": line, "page_title": page_title},
                    claim_url)
    gate, gate_removed, gate_url, as_of_version = g.ro_query(Q_FINDING_GATE, {"k": key}).result_set[0]
    link = g.ro_query(Q_LINK, {"c": claim_id, "g": gate}).result_set
    link_eid = None
    if link:
        method, conf, linker_conf, signals, vby, von, reason, lurl, elim = link[0]
        link_eid = add("edge", "ABOUT", f"Claim:{claim_id}", f"FeatureGate:{gate}",
                       {"method": method, "link_confidence": conf, "linker_confidence": linker_conf,
                        "signals": json.loads(signals) if signals else None, "verified_by": vby, "verified_on": von, "reason": reason,
                        "eliminated": json.loads(elim) if elim else []}, lurl)
    history = stage_ranges(g.ro_query(Q_HISTORY, {"g": gate}).result_set)
    hist_eids = [add("edge", "STAGE_IN", f"FeatureGate:{gate}", f"Release:{h['from']}..{h['to']}",
                     {"stage": h["stage"], "from": h["from"], "to": h["to"], "overlap": h["overlap"]}, h["source_url"]) for h in history]
    finding_eid = add("node", "Finding", f"Claim:{claim_id}", f"FeatureGate:{gate}",
                      {"key": key, "kind": kind, "severity": severity, "claimed": f"{c_stage} since {c_since}",
                       "actual_stages": actual, "as_of": as_of_version, "gate_removed": bool(removed), "message": message}, gate_url)

    keps = rank_keps(g.ro_query(Q_KEPS, {"g": gate}).result_set, gate)
    primary = keps[0] if keps else None
    kep_eid = sig_eid = None
    if primary:
        kep_eid = add("edge", "INTRODUCES", f"KEP:{primary['id']}", f"FeatureGate:{gate}",
                      {"method": primary["method"], "confidence": primary["confidence"], "title": primary["title"],
                       "status": primary["status"], "stage": primary["stage"], "milestone": primary["milestone"]}, primary["introduces_url"])
        if primary["sig"]:
            sig_eid = add("edge", "OWNED_BY", f"KEP:{primary['id']}", f"SIG:{primary['sig']}", {"sig_name": primary["sig_name"]}, primary["source_url"])
    kep_prs: list[dict] = []
    prs_strong = prs_weak = 0
    if primary:
        prs_strong, prs_weak = (g.ro_query(Q_KEP_PR_COUNTS, {"k": primary["id"], "min": PR_STRONG}).result_set or [[0, 0]])[0]
        for num, title, merged, url, method, conf, matched, issues in g.ro_query(Q_KEP_PRS, {"k": primary["id"], "min": PR_STRONG, "limit": 5}).result_set:
            issues = [{"id": i[0], "title": i[1], "state": i[2], "url": i[3]} for i in issues if i and i[0]]
            eid = add("edge", "IMPLEMENTS", f"PR:{num}", f"KEP:{primary['id']}", {"title": title, "merged_at": merged, "method": method, "confidence": conf, "evidence": matched}, url)
            for i in issues:
                add("edge", "FIXES", f"PR:{num}", f"Issue:{i['id']}", {"title": i["title"], "state": i["state"]}, i["url"])
            kep_prs.append({"number": num, "title": title, "merged_at": merged, "url": url, "method": method, "confidence": conf, "evidence": matched, "issues": issues, "evidence_id": eid})
    everyone = suggest_reviewers(g, page_path, primary["sig"] if primary else None, primary["id"] if primary else None, gate=gate)
    docs_group = [r for r in everyone if any(x["category"] == "docs" for x in r["reasons"])][:4]  # can merge the doc fix
    expert_group = [r for r in everyone if r not in docs_group and any(x["category"] != "docs" for x in r["reasons"])][:4]  # can check the facts
    reviewers = [{**r, "group": "docs approver"} for r in docs_group] + [{**r, "group": "domain expert"} for r in expert_group]
    reviewer_eids = [add("node", "Reviewer", f"Person:{r['handle']}", None,
                         {"group": r["group"], "score": r["score"], "reasons": [x["reason"] for x in r["reasons"]]},
                         next((x["source_url"] for x in r["reasons"] if x["source_url"]), None)) for r in reviewers]

    path = [f"DocPage:{page_path}", f"DocSection:{heading}", f"Claim:{c_stage} since {c_since}", f"FeatureGate:{gate}",
            f"Release:{as_of_version} ({'/'.join(actual)})"]
    if primary:
        path += [f"KEP:{primary['id']}"] + ([f"SIG:{primary['sig']}"] if primary["sig"] else [])
    bundle["path"] = path
    bundle["sections"].update({
        "finding": {"key": key, "kind": kind, "severity": severity, "message": message, "evidence_id": finding_eid,
                    "anchored_by": (g.ro_query("MATCH (f:Finding {key: $k}) RETURN f.anchored_by", {"k": key}).result_set or [[None]])[0][0]},
        "page": {"path": page_path, "url": page_url, "title": page_title},
        "claim": {"id": claim_id, "shortcode": shortcode, "line": line, "heading": heading, "stage": c_stage, "since": c_since,
                  "source_url": claim_url, "evidence_id": claim_eid},
        "link": ({"gate": gate, "method": link[0][0], "link_confidence": link[0][1], "linker_confidence": link[0][2],
                  "signals": json.loads(link[0][3]) if link[0][3] else None, "verified_by": link[0][4], "verified_on": link[0][5],
                  "reason": link[0][6], "source_url": link[0][7], "eliminated": json.loads(link[0][8]) if link[0][8] else [], "evidence_id": link_eid} if link else None),
        "gate": {"name": gate, "removed": bool(gate_removed), "source_url": gate_url, "history": history, "history_evidence": hist_eids},
        "kep": ({**primary, "evidence_id": kep_eid, "sig_evidence_id": sig_eid, "others": keps[1:], "prs": kep_prs, "prs_strong": prs_strong, "prs_weak": prs_weak} if primary else None),
        "reviewers": [{**r, "evidence_id": e} for r, e in zip(reviewers, reviewer_eids)],
    })
    return bundle
