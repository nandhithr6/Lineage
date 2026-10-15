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

Q_ACTIVITY = """
MATCH (c:CodePath)-[:CHILD_OF*0..]->(anc:CodePath {id: $id})
MATCH (pr:PR)-[:TOUCHES]->(c)
WHERE NOT pr.cherry_pick AND NOT pr.cross_cutting
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


def orphaned_components(g: Graph, months: int = 12, today: datetime | None = None, repo: str = "kubernetes") -> list[dict]:
    """Folders with listed approvers where NONE of them was active in the last `months` (as reviewer, approver or author on a PR touching it)."""
    rows = g.ro_query("MATCH (c:CodePath {repo: $r}) WHERE c.has_owners AND c.path <> '' RETURN c.path ORDER BY c.path", {"r": repo}).result_set
    out = []
    for (path,) in rows:
        v = official_vs_actual(g, repo, path, months, today)
        if v["listed_approvers"] and not v["active_approvers"]:
            out.append({"path": path, "listed_approvers": v["listed_approvers"], "active_people": v["active_people"], "months": months, "unlisted_active": v["unlisted_active"][:5]})
    return out
