"""How two things in the knowledge graph connect: the shortest weighted path between them, computed INSIDE FalkorDB with `CALL algo.SPpaths`.

FalkorDB's SPpaths needs `sourceNode`, `targetNode`, `relTypes` and a numeric `weightProp` on every edge it may use, so the loader gives each relationship type listed below a `weight`:
1 for structural links (ownership, membership, design decisions, docs structure) and 2 for activity links (reviewed, approved, touched), so a path prefers "is an approver of the folder
this team owns" over "reviewed a pull request that touched it", and says which kind it used. Traversal is undirected (`relDirection: 'both'`); each hop reports its true direction.
"""
from __future__ import annotations

from falkordb import Graph
from falkordb.node import Node

# relationship type -> weight. Anything not listed is never used by path_between. Only STRUCTURAL links (who owns, who belongs, which decision introduced what, how the docs are laid out):
# the high-volume activity edges (REVIEWED, APPROVED, TOUCHES, STAGE_IN, ...) are left out on purpose, because with them a 5-hop search over 30,000 edges does not finish in time
# (measured: 14-16 structural types, 4 hops 0.4 s, 5 hops 4 s; with the activity edges it timed out).
PATH_WEIGHTS: dict[str, int] = {
    "APPROVER_OF": 1, "REVIEWER_OF": 1, "EMERITUS_APPROVER_OF": 1, "MEMBER_OF": 1, "OWNS": 1, "CHILD_OF": 1, "OWNED_BY": 1, "INTRODUCES": 1, "ABOUT": 1, "HAS_CLAIM": 1,
    "HAS_SECTION": 1, "IN_PATH": 1, "CHAIRS": 1, "TECH_LEAD_OF": 1, "AUTHORED": 2, "APPROVED_KEP": 2,  # having approved some design decision is a weaker tie than owning the code, so it costs 2
}
LABEL_KEY = {"FeatureGate": "name", "Person": "handle", "SIG": "id", "Team": "id", "KEP": "id", "CodePath": "id", "DocPage": "path", "DocSection": "id", "Claim": "id", "Release": "num", "PR": "number", "Issue": "id"}
MAX_LEN = 4  # default hops; the hard cap is 5 (see the measurement above)
HARD_MAX_LEN = 5
TIMEOUT_MS = 6000


class PathTimeout(RuntimeError):
    pass


def set_path_weights(g: Graph) -> None:
    """Idempotent: give every relationship of a path-eligible type its weight (a one-time pass after loading)."""
    for rel, w in PATH_WEIGHTS.items():
        g.query(f"MATCH ()-[e:{rel}]->() SET e.weight = $w", {"w": w})


def node_ref(n: Node) -> str:
    label = n.labels[0] if n.labels else "Node"
    return f"{label}:{n.properties.get(LABEL_KEY.get(label, 'id'))}"


def shortest_path(g: Graph, a: tuple[str, str], b: tuple[str, str], max_len: int = MAX_LEN) -> dict | None:
    """The cheapest path between two nodes, or None when none exists within `max_len` hops. a, b = (label, key). Returns {"weight", "nodes": [...], "hops": [...]}."""
    (la, ka), (lb, kb) = a, b
    if la not in LABEL_KEY or lb not in LABEL_KEY:
        raise ValueError(f"unsupported label: {la!r} / {lb!r}")
    q = (f"MATCH (a:{la} {{{LABEL_KEY[la]}: $a}}), (b:{lb} {{{LABEL_KEY[lb]}: $b}}) "
         "CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: $rels, relDirection: 'both', weightProp: 'weight', pathCount: 1, maxLen: $maxlen}) "
         "YIELD path, pathWeight RETURN path, pathWeight ORDER BY pathWeight LIMIT 1")
    try:
        rows = g.ro_query(q, {"a": ka, "b": kb, "rels": list(PATH_WEIGHTS), "maxlen": min(max(int(max_len), 1), HARD_MAX_LEN)}, timeout=TIMEOUT_MS).result_set
    except Exception as e:  # FalkorDB reports a timeout as a plain ResponseError
        if "timed out" in str(e).lower():
            raise PathTimeout(f"the path search did not finish within {TIMEOUT_MS // 1000} s") from e
        raise
    if not rows:
        return None
    path, weight = rows[0]
    nodes, edges = path.nodes(), path.edges()
    hops = []
    for i, e in enumerate(edges):
        forward = e.src_node == nodes[i].id
        hops.append({"from": node_ref(nodes[i]), "to": node_ref(nodes[i + 1]), "type": e.relation, "direction": "forward" if forward else "backward",
                     "source_url": e.properties.get("source_url"), "props": {k: v for k, v in e.properties.items() if k in ("valid_from", "valid_to", "method", "confidence")}})
    return {"weight": weight, "nodes": [node_ref(n) for n in nodes], "hops": hops}


def describe_path(p: dict) -> str:
    """'alice -[APPROVER_OF]-> CodePath:kubernetes:pkg/volume <-[OWNS]- SIG:sig-storage ...' as one readable line."""
    out = [p["nodes"][0].split(":", 1)[1]]
    for h, nxt in zip(p["hops"], p["nodes"][1:]):
        arrow = f"-[{h['type']}]->" if h["direction"] == "forward" else f"<-[{h['type']}]-"
        out += [arrow, nxt.split(":", 1)[1] if nxt.startswith("Person:") else nxt]
    return " ".join(out)
