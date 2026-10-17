"""Curated, parameterised Cypher queries. Each returns plain rows with the source_url that proves them.

effective_owners walks UP the folder tree (CHILD_OF edges), collecting the owners declared on every folder it passes,
and stops after a folder whose OWNERS sets `no_parent_owners: true`. Time travel: as_of is a sampled release number
(130 = 1.30, 138 = latest); an edge counts when valid_from <= as_of and (valid_to is null or valid_to > as_of).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from falkordb import Graph

LATEST = 138  # 1.38-dev: the HEAD sample

Q_WALK_UP = """
MATCH (start:CodePath {id: $start})
MATCH p = (start)-[:CHILD_OF*0..]->(anc:CodePath)
WHERE none(n IN nodes(p)[0..length(p)] WHERE n.no_parent_owners)
WITH anc, length(p) AS hops
MATCH (o)-[r:APPROVER_OF|REVIEWER_OF|EMERITUS_APPROVER_OF]->(anc)
WHERE r.valid_from <= $as_of AND (r.valid_to IS NULL OR r.valid_to > $as_of)
RETURN anc.id, anc.path, hops, type(r), labels(o)[0], coalesce(o.handle, o.id), r.filter, r.source_url, r.valid_from, r.valid_to
"""

Q_MEMBERS = """
MATCH (p:Person)-[m:MEMBER_OF]->(t:Team {id: $team})
WHERE m.valid_from <= $as_of AND (m.valid_to IS NULL OR m.valid_to > $as_of)
RETURN p.handle, m.source_url
"""


def effective_owners(g: Graph, repo: str, path: str, as_of: int = LATEST, is_file: bool = False) -> list[dict]:
    """Owners of a folder (or of a file, with is_file=True) in `repo`, with inheritance and aliases resolved.

    Returns rows {role, handle, via_team, declared_at, hops, source_url, member_source_url, valid_from, valid_to}.
    """
    directory = path.rsplit("/", 1)[0] if is_file and "/" in path else ("" if is_file else path)
    rows = g.ro_query(Q_WALK_UP, {"start": f"{repo}:{directory}", "as_of": as_of}).result_set
    out: list[dict] = []
    members_cache: dict[str, list] = {}
    for anc_id, anc_path, hops, role, kind, subject, flt, url, vfrom, vto in rows:
        if flt and is_file:  # a filter limits the entry to files whose path (relative to the OWNERS folder) matches
            rel = path[len(anc_path) + 1 :] if anc_path else path
            if not re.search(flt, rel):
                continue
        elif flt:  # folder query: cannot tell which files the filter covers, so skip rather than overclaim
            continue
        base = {"role": role, "declared_at": anc_path, "hops": hops, "source_url": url, "valid_from": vfrom, "valid_to": vto}
        if kind == "Team":
            if subject not in members_cache:
                members_cache[subject] = g.ro_query(Q_MEMBERS, {"team": subject, "as_of": as_of}).result_set
            for handle, member_url in members_cache[subject]:
                out.append({**base, "handle": handle, "via_team": subject, "member_source_url": member_url})
        else:
            out.append({**base, "handle": subject, "via_team": None, "member_source_url": None})
    return sorted(out, key=lambda r: (r["role"], r["hops"], r["handle"]))


SAMPLED_RELEASES = [130, 131, 132, 133, 134, 135, 136, 137, 138]  # 138 = HEAD (1.38-dev)


def release_label(num: int) -> str:
    return "latest (1.38-dev)" if num == LATEST else f"{num // 100}.{num % 100}"


def owner_keys(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """(handle, role) -> the first row that shows it; teams are already expanded to people by effective_owners."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        out.setdefault((r["handle"], r["role"]), r)
    return out


def owners_diff(g: Graph, repo: str, path: str, from_num: int, to_num: int, is_file: bool = False) -> dict:
    """Who gained or lost an ownership role on a folder/file (inheritance and teams resolved) between two sampled releases."""
    before = owner_keys(effective_owners(g, repo, path, from_num, is_file))
    after = owner_keys(effective_owners(g, repo, path, to_num, is_file))
    added = [after[k] for k in sorted(after.keys() - before.keys())]
    removed = [before[k] for k in sorted(before.keys() - after.keys())]
    return {"added": added, "removed": removed, "unchanged": len(before.keys() & after.keys())}


Q_STAGE_AT = "MATCH (g:FeatureGate {name: $g})-[e:STAGE_IN]->(r:Release {num: $n}) RETURN e.stage, e.source_url"


def gate_stage_change(g: Graph, gate: str, from_num: int, to_num: int) -> dict:
    """Stage(s) of a gate at two releases (empty list = the gate had no stage then: not yet introduced, or already removed)."""
    at = lambda n: g.ro_query(Q_STAGE_AT, {"g": gate, "n": n}).result_set  # noqa: E731
    a, b = at(from_num), at(to_num)
    return {"from": sorted({r[0] for r in a}), "to": sorted({r[0] for r in b}), "source_url": (b or a or [[None, None]])[0][1]}


# --- activity (needs the PR snapshot) ----------------------------------------------------------------------------
HALF_LIFE_DAYS = 180.0  # a review this old counts half as much as one today
APPROVAL_WEIGHT = 1.5

# FalkorDB checks only that AT LEAST ONE matching relationship exists when a relationship in a pattern is not referenced elsewhere (known-limitations page), which
# silently drops parallel edges. Every relationship below is therefore bound and referenced (the path / ID(t) in WHERE), even where today's data has no parallel edges.
Q_ACTIVITY = """
MATCH chain = (c:CodePath)-[:CHILD_OF*0..]->(anc:CodePath {id: $id})
MATCH (pr:PR)-[t:TOUCHES]->(c)
WHERE NOT pr.cherry_pick AND NOT pr.cross_cutting AND length(chain) >= 0 AND ID(t) >= 0
MATCH (p:Person)-[r:REVIEWED|APPROVED|AUTHORED]->(pr)
RETURN p.handle, type(r), r.at, pr.number, pr.url, pr.title
"""


def parse_when(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def activity_rows(g: Graph, repo: str, path: str) -> list[list]:
    return g.ro_query(Q_ACTIVITY, {"id": f"{repo}:{path.strip('/')}"}).result_set


def experts_for(g: Graph, repo: str, path: str, today: datetime | None = None, limit: int = 10) -> list[dict]:
    """Who actually reviews (and approves) changes under a folder, most recent activity weighted most (180-day half-life).

    Counts each PR once per person. Automated cherry-picks (release-process approvals) and cross-cutting PRs (10+ code paths, or too many files to list) are
    excluded: they say little about who knows one folder. Only PRs labelled sig/storage, sig/node or sig/apps are loaded, so this is a floor, not a census.
    """
    today = today or datetime.now(timezone.utc)
    per: dict[str, dict] = {}
    for handle, kind, at, number, url, title in activity_rows(g, repo, path):
        when = parse_when(at)
        if kind == "AUTHORED" or when is None:
            continue
        e = per.setdefault(handle, {"handle": handle, "prs": {}, "approvals": set(), "last": None})
        e["prs"][number] = (url, title, when)
        if kind == "APPROVED":
            e["approvals"].add(number)
        if e["last"] is None or when > e["last"]:
            e["last"] = when
    out = []
    for e in per.values():
        score = 0.0
        for number, (_, _, when) in e["prs"].items():
            age = max(0.0, (today - when).total_seconds() / 86400)
            score += (APPROVAL_WEIGHT if number in e["approvals"] else 1.0) * 0.5 ** (age / HALF_LIFE_DAYS)
        examples = sorted(e["prs"].items(), key=lambda kv: kv[1][2], reverse=True)[:3]
        out.append({"handle": e["handle"], "score": round(score, 2), "prs_reviewed": len(e["prs"]), "approvals": len(e["approvals"]), "last_active": e["last"].date().isoformat(),
                    "examples": [{"number": n, "url": u, "title": ti, "at": w.date().isoformat()} for n, (u, ti, w) in examples]})
    return sorted(out, key=lambda r: (-r["score"], r["handle"]))[:limit]


def official_vs_actual(g: Graph, repo: str, path: str, months: int = 12, today: datetime | None = None) -> dict:
    """Listed approvers who were active in the last `months`, listed approvers who were not, and active reviewers who are not listed."""
    today = today or datetime.now(timezone.utc)
    cutoff = today.timestamp() - months * 30.44 * 86400
    listed = {r["handle"] for r in effective_owners(g, repo, path) if r["role"] == "APPROVER_OF"}
    listed_any = {r["handle"] for r in effective_owners(g, repo, path) if r["role"] in ("APPROVER_OF", "REVIEWER_OF")}
    active: dict[str, datetime] = {}
    for handle, kind, at, *_ in activity_rows(g, repo, path):
        when = parse_when(at)
        if when and when.timestamp() >= cutoff and (handle not in active or when > active[handle]):
            active[handle] = when
    return {"active_approvers": sorted(h for h in listed if h in active), "inactive_approvers": sorted(h for h in listed if h not in active),
            "unlisted_active": sorted(h for h in active if h not in listed_any), "listed_approvers": len(listed), "active_people": len(active)}


def scan_ownership(g: Graph, months: int = 12, today: datetime | None = None, repo: str = "kubernetes") -> list[tuple[str, dict]]:
    """official_vs_actual for every folder that lists approvers. About 10 s on the real graph, so callers that need several views (orphaned, dormant, gaps) compute it once and pass it in."""
    rows = g.ro_query("MATCH (c:CodePath {repo: $r}) WHERE c.has_owners AND c.path <> '' RETURN c.path ORDER BY c.path", {"r": repo}).result_set
    out = []
    for (path,) in rows:
        v = official_vs_actual(g, repo, path, months, today)
        if v["listed_approvers"]:
            out.append((path, v))
    return out


def _no_active_approver(g: Graph, months: int, today: datetime | None, repo: str, scan: list | None = None) -> list[tuple[str, dict]]:
    return [(p, v) for p, v in (scan if scan is not None else scan_ownership(g, months, today, repo)) if not v["active_approvers"]]


def orphaned_components(g: Graph, months: int = 12, today: datetime | None = None, repo: str = "kubernetes", scan: list | None = None) -> list[dict]:
    """ORPHANED: folders with listed approvers, none of whom was active in the last `months`, while OTHER people did work there (a PR touched the folder).
    That is the risk case: the code is alive but nobody listed is looking after it. Folders with no activity at all are `dormant_components`, not a risk."""
    return [{"path": p, "listed_approvers": v["listed_approvers"], "active_people": v["active_people"], "months": months, "unlisted_active": v["unlisted_active"][:5]}
            for p, v in _no_active_approver(g, months, today, repo, scan) if v["active_people"] > 0]


def dormant_components(g: Graph, months: int = 12, today: datetime | None = None, repo: str = "kubernetes", scan: list | None = None) -> list[dict]:
    """DORMANT: folders with listed approvers where nobody did anything in the window. Often stable code, so this is information, not a risk."""
    return [{"path": p, "listed_approvers": v["listed_approvers"], "months": months}
            for p, v in _no_active_approver(g, months, today, repo, scan) if v["active_people"] == 0]


# --- FalkorDB graph algorithm: PageRank over the review network ---------------------------------------------------------------
# (author)-[:REVIEWED_BY]->(reviewer) edges are derived from PR reviews (loader). algo.pageRank takes ONE label and ONE relationship type as positional
# arguments (FalkorDB docs) and ignores edge weights, so every author-reviewer pair is one edge. Rank flows from author to reviewer, so
# being relied on by people who are themselves relied on counts most (a reviewer who is the last stop of a busy chain can outrank the busy hub that feeds it).
# It measures position in the review network, NOT code ownership or skill.
Q_PAGERANK = """
CALL algo.pageRank('Person', 'REVIEWED_BY') YIELD node, score
WITH node, score ORDER BY score DESC LIMIT $limit
RETURN node.handle, score
"""
Q_NET_STATS = """
MATCH (p:Person) WHERE p.handle IN $handles
OPTIONAL MATCH (x:Person)-[rb:REVIEWED_BY]->(p)
WITH p, count(DISTINCT x) AS authors, coalesce(sum(rb.count), 0) AS reviews
OPTIONAL MATCH (p)-[a:APPROVER_OF]->(c:CodePath) WHERE a.valid_to IS NULL
RETURN p.handle, authors, reviews, count(DISTINCT c)
"""


def review_network_top(g: Graph, limit: int = 15) -> list[dict]:
    """Most central reviewers by PageRank, with how many distinct authors rely on them and whether OWNERS lists them as an approver today."""
    ranked = g.ro_query(Q_PAGERANK, {"limit": limit}).result_set
    if not ranked:
        return []
    stats = {h: (authors, reviews, approver_of) for h, authors, reviews, approver_of in g.ro_query(Q_NET_STATS, {"handles": [h for h, _ in ranked]}).result_set}
    return [{"rank": i, "handle": h, "score": round(score, 5), "authors_served": stats.get(h, (0, 0, 0))[0], "reviews": stats.get(h, (0, 0, 0))[1],
             "approver_of_folders": stats.get(h, (0, 0, 0))[2], "listed_approver": stats.get(h, (0, 0, 0))[2] > 0}
            for i, (h, score) in enumerate(ranked, 1)]


_RANKS: dict = {}


def network_ranks(g: Graph, ttl: int = 300) -> dict[str, dict]:
    """handle -> {rank, of, score, authors_served} for EVERY person with review-network edges (one PageRank run, cached for `ttl` seconds)."""
    import time

    hit = _RANKS.get(g.name)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    ranked = g.ro_query(Q_PAGERANK, {"limit": 100000}).result_set
    authors = {h: n for h, n in g.ro_query("MATCH (a:Person)-[r:REVIEWED_BY]->(p:Person) WHERE ID(r) >= 0 RETURN p.handle, count(DISTINCT a)").result_set}
    out = {h: {"rank": i, "of": len(ranked), "score": round(s, 5), "authors_served": authors.get(h, 0)} for i, (h, s) in enumerate(ranked, 1) if authors.get(h, 0) > 0}
    _RANKS[g.name] = (time.time(), out)
    return out


# Test and tooling folders inherit one large shared approver list, which makes the quiet share look dramatic without meaning an owner is missing.
GAP_SKIP = ("test/", "hack/", "vendor/", "third_party/")


def ownership_gaps(g: Graph, months: int = 12, today: datetime | None = None, repo: str = "kubernetes", limit: int = 15, scan: list | None = None) -> list[dict]:
    """Official versus actual ownership for every in-scope folder, most worrying first: many listed approvers who have gone quiet, or none active at all.

    Only folders under sig-storage, sig-node and sig-apps are covered (the PR data we have); a folder with no recorded activity at all is flagged `no_activity_data`.
    """
    out = []
    for path, v in (scan if scan is not None else scan_ownership(g, months, today, repo)):
        if path.startswith(GAP_SKIP):
            continue
        quiet = len(v["inactive_approvers"])
        out.append({"path": path, "listed_approvers": v["listed_approvers"], "active_approvers": len(v["active_approvers"]), "inactive_approvers": quiet,
                    "inactive_share": round(quiet / v["listed_approvers"], 2), "active_people": v["active_people"], "other_active_people": len(v["unlisted_active"]),
                    "no_activity_data": v["active_people"] == 0, "inactive_examples": v["inactive_approvers"][:5]})
    out.sort(key=lambda r: (-(r["active_approvers"] == 0 and not r["no_activity_data"]), -r["inactive_share"], -r["inactive_approvers"], r["path"]))
    return out[:limit]
