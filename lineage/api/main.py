"""FastAPI app: the graph over HTTP. Every endpoint is read-only except that /ask talks to the LLM provider.

Run: `uv run --env-file .env uvicorn lineage.api.main:app --reload`  (interactive docs at /docs)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from falkordb import Graph
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from lineage.agent.llm import LLM, LLMNotConfigured, ProviderError, build_llm
from lineage.agent.loop import run_agent
from lineage.agent.tools import ToolBox
from lineage.api.models import (AboutData, AskRequest, AskResponse, ChangesResponse, Edge, EntityResponse, FindingDetail, FindingList,
                                FindingSummary, FixResponse, Health, Node, OwnerRow, OwnersResponse, ExpertRow, ExpertsResponse, OrphanRow)
from lineage.config import llm_configured, load_settings
from lineage.detect.fix import draft_fix
from lineage.graph.client import connect
from lineage.agent.tools import release_num
from lineage.graph.queries import SAMPLED_RELEASES, effective_owners, experts_for, gate_stage_change, official_vs_actual, orphaned_components, owners_diff, release_label
from lineage.graph.trace import SEVERITY_ORDER, build_trace

# Make our own log lines (which LLM provider answered each request, fallbacks, retries) visible in the server log. The HTTP library stays at WARNING (it logs URLs).
_log = logging.getLogger("lineage")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _log.addHandler(_h)
_log.setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="Lineage", version="0.1.0",
              description="A company brain that knows when its knowledge is out of date, and proves every answer. "
                          "Built on public Kubernetes project data; not an official Kubernetes or CNCF tool.")
app.add_middleware(CORSMiddleware, allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o],
                   allow_methods=["GET", "POST"], allow_headers=["*"])

MANIFEST = Path("snapshot/manifest.json")
SESSIONS: dict[str, list[dict]] = defaultdict(list)  # in-memory conversation history, text only
MAX_TURNS = 8


# --- dependencies (overridable in tests) ---------------------------------------------------------------------
@lru_cache
def _graph() -> Graph:
    s = load_settings()
    return connect(s).select_graph(s.falkordb_graph)


def get_graph() -> Graph:
    return _graph()


def get_llm() -> LLM:
    try:
        return build_llm()
    except LLMNotConfigured as e:
        raise HTTPException(503, str(e))


def finding_id(key: str) -> str:
    """Short stable id for URLs (finding keys contain '|', '#' and '/'). Derived from the key, so it survives rebuilds."""
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def resolve_key(g: Graph, ident: str) -> str:
    keys = [r[0] for r in g.ro_query("MATCH (f:Finding {active: true}) RETURN f.key").result_set]
    for k in keys:
        if ident in (k, finding_id(k)):
            return k
    raise HTTPException(404, f"No active finding {ident!r}")


# --- endpoints -----------------------------------------------------------------------------------------------
@app.get("/health", response_model=Health)
def health(g: Graph = Depends(get_graph)) -> Health:
    s = load_settings()
    llm_ok = llm_configured(s)
    try:
        g.ro_query("RETURN 1")
        db = True
        detail = None
    except Exception as e:  # report, do not crash
        db, detail = False, str(e)[:120]
    return Health(status="ok" if db else "degraded", database=db, graph=g.name, llm_configured=llm_ok, detail=detail)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, g: Graph = Depends(get_graph), llm: LLM = Depends(get_llm)) -> AskResponse:
    history = SESSIONS[req.session_id][-2 * MAX_TURNS:] if req.session_id else []
    try:
        res = run_agent(llm, ToolBox(g), req.question, history)
    except ProviderError as e:  # every provider failed after retries: say so plainly instead of a 500
        raise HTTPException(503, f"The language model is unavailable right now ({str(e)[:160]}). Findings and the graph still work.")
    if req.session_id:
        SESSIONS[req.session_id] += [{"role": "user", "content": req.question}, {"role": "assistant", "content": res.answer}]
    return AskResponse(answer=res.answer, status=res.status, citations=res.citations, evidence=res.evidence, path=res.path,
                       tool_trace=res.tool_trace, unsupported_citations=res.unsupported_citations, session_id=req.session_id)


Q_LIST = """
MATCH (f:Finding {active: true})-[:FOR_CLAIM]->(c:Claim)<-[:HAS_CLAIM]-(s:DocSection)<-[:HAS_SECTION]-(p:DocPage), (f)-[:FOR_GATE]->(g:FeatureGate)
OPTIONAL MATCH (c)-[a:ABOUT]->(g)
OPTIONAL MATCH (k:KEP)-[:INTRODUCES]->(g), (k)-[:OWNED_BY]->(sg:SIG)
RETURN f.key, f.kind, f.severity, f.message, p.title, p.url, s.heading, f.claimed_stage, f.claimed_since, g.name,
       a.link_confidence, a.method, c.source_url, collect(DISTINCT sg.id)
"""


@app.get("/findings", response_model=FindingList)
def findings(sig: str | None = None, kind: str | None = None, severity: str | None = Query(None, pattern="^(finding|minor|review)$"),
             min_conf: float = Query(0.0, ge=0, le=1), g: Graph = Depends(get_graph)) -> FindingList:
    rows = g.ro_query(Q_LIST).result_set
    out, counts = [], defaultdict(int)
    for key, k, sev, msg, title, url, heading, stage, since, gate, conf, method, src, sigs in rows:
        if (kind and k != kind) or (severity and sev != severity) or (sig and sig not in sigs) or (conf or 0) < min_conf:
            continue
        counts[k] += 1
        out.append(FindingSummary(id=finding_id(key), key=key, kind=k, severity=sev, message=msg, page_title=title, page_url=url, section=heading,
                                  claimed_stage=stage, claimed_since=since, gate=gate, link_confidence=conf, link_method=method, source_url=src))
    out.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.page_title or "", f.section or ""))
    return FindingList(total=len(out), counts=dict(counts), findings=out)


@app.get("/findings/{ident:path}/fix", include_in_schema=False)
def _fix_get_hint(ident: str):  # a GET on the fix URL is a common mistake; say what to do instead of a bare 405
    raise HTTPException(405, "Use POST /findings/{id}/fix")


@app.post("/findings/{ident:path}/fix", response_model=FixResponse)
def finding_fix(ident: str, g: Graph = Depends(get_graph)) -> FixResponse:
    return FixResponse(**draft_fix(g, resolve_key(g, ident)))


@app.get("/findings/{ident:path}", response_model=FindingDetail)
def finding_detail(ident: str, g: Graph = Depends(get_graph)) -> FindingDetail:
    key = resolve_key(g, ident)
    b = build_trace(g, None, finding_key=key)
    return FindingDetail(key=key, status=b["status"], path=b["path"], evidence=b["evidence"], sections=b["sections"])


def _rows(rows: list[dict]) -> list[OwnerRow]:
    return [OwnerRow(handle=r["handle"], role=r["role"], via_team=r["via_team"], declared_at=r["declared_at"], hops=r["hops"], source_url=r["source_url"],
                     valid_from_release=r["valid_from"], valid_to_release=r["valid_to"]) for r in rows]


def _num(rel: str, repo: str) -> int:
    n = release_num(rel)
    if repo == "website" and n != 138:
        raise HTTPException(422, "Docs (website) ownership is sampled at the latest commit only; use as_of=latest.")
    if n not in SAMPLED_RELEASES:
        raise HTTPException(422, f"Ownership history is sampled at releases 1.30-1.37 and latest; {rel!r} is not one of them.")
    return n


@app.get("/owners/{repo}/{path:path}", response_model=OwnersResponse)
def owners(repo: str, path: str, as_of: str = "latest", g: Graph = Depends(get_graph)) -> OwnersResponse:
    """Who approves/reviews a folder or file (teams resolved to people, inheritance applied), optionally as of a sampled release."""
    if repo not in ("kubernetes", "website"):
        raise HTTPException(404, "repo must be 'kubernetes' or 'website'")
    n = _num(as_of, repo)
    p = path.strip("/")
    if not g.ro_query("MATCH (c:CodePath {id: $i}) RETURN c.id", {"i": f"{repo}:{p}"}).result_set:
        if p and not p.endswith((".md", ".go", ".yaml", ".py")):
            raise HTTPException(404, f"No folder {p!r} in {repo} (only folders on the in-scope SIG paths and the docs tree are loaded).")
    rows = effective_owners(g, repo, p, n, is_file=p.endswith((".md", ".go", ".yaml", ".py")))
    return OwnersResponse(repo=repo, path=p, as_of=release_label(n), as_of_num=n, declared_at=sorted({r["declared_at"] or "/" for r in rows}), owners=_rows(rows),
                          available_releases=[release_label(x) for x in (SAMPLED_RELEASES if repo == "kubernetes" else [138])],
                          note=None if rows else "No owners found for this path at that release.")


@app.get("/changes", response_model=ChangesResponse)
def changes(entity: str, from_release: str = Query(..., alias="from"), to_release: str = Query(..., alias="to"), repo: str = "kubernetes",
            g: Graph = Depends(get_graph)) -> ChangesResponse:
    """What changed between two releases: a feature gate's stage, or who gained/lost an ownership role on a path."""
    if g.ro_query("MATCH (x:FeatureGate {name: $g}) RETURN x.name", {"g": entity}).result_set:
        c = gate_stage_change(g, entity, release_num(from_release), release_num(to_release))
        return ChangesResponse(entity=entity, kind="gate", from_release=from_release, to_release=to_release, gate_stage_then=c["from"], gate_stage_now=c["to"], source_url=c["source_url"])
    if repo != "kubernetes":
        raise HTTPException(422, "Ownership changes need history, which exists for kubernetes/kubernetes only.")
    a, b = _num(from_release, repo), _num(to_release, repo)
    p = entity.strip("/")
    d = owners_diff(g, repo, p, a, b, is_file=p.endswith((".md", ".go", ".yaml", ".py")))
    return ChangesResponse(entity=p, kind="path", from_release=release_label(a), to_release=release_label(b), added=_rows(d["added"]), removed=_rows(d["removed"]), unchanged=d["unchanged"])


COVERAGE_NOTE = ToolBox.COVERAGE


@app.get("/experts/{repo}/{path:path}", response_model=ExpertsResponse)
def experts(repo: str, path: str, months: int = Query(12, ge=1, le=36), g: Graph = Depends(get_graph)) -> ExpertsResponse:
    """Who actually reviews a code folder (recency-weighted) versus who OWNERS lists. Needs the PR snapshot; code folders only."""
    if repo != "kubernetes":
        raise HTTPException(422, "Activity data covers kubernetes/kubernetes code only.")
    p = path.strip("/")
    ex = experts_for(g, repo, p)
    v = official_vs_actual(g, repo, p, months)
    listed = set(v["active_approvers"]) | set(v["inactive_approvers"])
    return ExpertsResponse(repo=repo, path=p, months=months, coverage_note=COVERAGE_NOTE, listed_and_active=v["active_approvers"], listed_but_inactive=v["inactive_approvers"],
                           active_but_not_listed=v["unlisted_active"],
                           experts=[ExpertRow(handle=e["handle"], recency_weighted_score=e["score"], prs_reviewed=e["prs_reviewed"], approvals=e["approvals"], last_active=e["last_active"],
                                              listed_approver=e["handle"] in listed, example_pr=e["examples"][0]["url"]) for e in ex])


@app.get("/orphans", response_model=list[OrphanRow])
def orphans(months: int = Query(12, ge=1, le=36), g: Graph = Depends(get_graph)) -> list[OrphanRow]:
    """Code folders whose listed approvers have all been inactive for `months` (sig-storage, sig-node and sig-apps PR data only)."""
    return [OrphanRow(path=o["path"], listed_approvers=o["listed_approvers"], active_people=o["active_people"], active_but_not_listed=o["unlisted_active"]) for o in orphaned_components(g, months)]


KEYS = {"Release": "num", "FeatureGate": "name", "DocPage": "path", "DocSection": "id", "Claim": "id", "KEP": "id", "SIG": "id",
        "Person": "handle", "CodePath": "id", "Team": "id", "Finding": "key"}


@app.get("/entity/{label}/{key:path}", response_model=EntityResponse)
def entity(label: str, key: str, limit: int = Query(40, ge=1, le=100), g: Graph = Depends(get_graph)) -> EntityResponse:
    if label not in KEYS:
        raise HTTPException(404, f"Unknown label {label!r}; choose one of {sorted(KEYS)}")
    prop = KEYS[label]
    val: object = int(key) if prop == "num" and key.isdigit() else key
    n = g.ro_query(f"MATCH (n:{label} {{{prop}: $k}}) RETURN n", {"k": val}).result_set
    if not n:
        raise HTTPException(404, f"No {label} with {prop}={key!r}")

    def node_of(x) -> Node:
        lab = x.labels[0]
        ident = x.properties.get(KEYS.get(lab, ""), "")
        props = {k: v for k, v in x.properties.items() if k not in ("embedding", "section_text", "text", "summary", "description", "mission")}
        return Node(id=f"{lab}:{ident}", label=lab, props=props)

    rows = g.ro_query(f"MATCH (n:{label} {{{prop}: $k}})-[r]-(m) RETURN r, m LIMIT {limit + 1}", {"k": val}).result_set
    me_internal = n[0][0].id  # internal id, used only inside this request to tell edge direction
    me = node_of(n[0][0])
    neighbors: dict[str, Node] = {}
    edges: list[Edge] = []
    for r, m in rows[:limit]:
        outgoing = r.src_node == me_internal
        mn = node_of(m)
        neighbors[mn.id] = mn
        a, b = (me.id, mn.id) if outgoing else (mn.id, me.id)
        edges.append(Edge(source=a, target=b, type=r.relation, props={k: v for k, v in r.properties.items() if k != "recorded_at"}))
    return EntityResponse(node=me, neighbors=list(neighbors.values()), edges=edges, truncated=len(rows) > limit)


@app.get("/about-data", response_model=AboutData)
def about_data(g: Graph = Depends(get_graph)) -> AboutData:
    from lineage.detect.drift import run_detector
    from lineage.graph.loader import counts

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    _, stats = run_detector(g)
    manual = g.ro_query("MATCH ()-[a:ABOUT {method: 'manual'}]->() RETURN count(a)").result_set[0][0]
    return AboutData(snapshot_date=manifest.get("snapshot_date"), sources=manifest.get("sources", []), counts=counts(g), detector=stats,
                     manual_links=manual, limitations=[
                         "Hard-coded labels are linked to gates by inference; only anchored or hand-verified links can produce a finding, the rest are 'possible drift' for a human to confirm.",
                         "Many docs labels describe features that never had a feature gate, so they cannot be checked.",
                         "Ownership history is sampled at release tags 1.30-1.37 and HEAD; docs (website) ownership is HEAD only.",
                         "Pull-request and review data is not loaded yet, so 'who actually reviews' is not available.",
                         "Lineage only drafts. It never posts to Kubernetes.",
                     ])
