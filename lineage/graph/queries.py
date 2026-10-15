"""Curated, parameterised Cypher queries. Each returns plain rows with the source_url that proves them.

effective_owners walks UP the folder tree (CHILD_OF edges), collecting the owners declared on every folder it passes,
and stops after a folder whose OWNERS sets `no_parent_owners: true`. Time travel: as_of is a sampled release number
(130 = 1.30, 138 = latest); an edge counts when valid_from <= as_of and (valid_to is null or valid_to > as_of).
"""
from __future__ import annotations

import re

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
