"""FastAPI app: the graph over HTTP. Every endpoint is read-only except that /ask talks to the LLM provider.

Run: `uv run --env-file .env uvicorn lineage.api.main:app --reload`  (interactive docs at /docs)
"""
from __future__ import annotations

import hashlib
import json
import uuid
import logging
import os
from collections import defaultdict
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from falkordb import Graph
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from lineage.agent.llm import LLM, LLMNotConfigured, ProviderError, build_llm
from lineage.agent.answer_cache import AnswerCache
from lineage.agent.loop import preflight_result, run_agent
from lineage.agent.tools import ToolBox
from lineage.api.models import (AboutData, AskRequest, AskResponse, ChangesResponse, Edge, EntityResponse, FindingDetail, FindingList,
                                FindingSummary, FixResponse, Health, Node, OwnerRow, OwnersResponse, ExpertRow, ExpertsResponse, OrphanRow, GapRow, KeyReviewer, InsightsResponse, DormantRow, ChangesSince, SnapshotInfo, SnapshotList, SearchResponse, SaveNoteRequest, SchemaEdge, SchemaNode, SchemaResponse)
from lineage.config import llm_configured, load_settings
from lineage.detect.fix import draft_fix
from lineage.graph.client import connect
from lineage.agent.tools import release_num
from lineage.graph.queries import SAMPLED_RELEASES, effective_owners, experts_for, gate_stage_change, official_vs_actual, dormant_components, orphaned_components, owners_diff, scan_ownership, ownership_gaps, release_label, review_network_top
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

@app.on_event("startup")
def _warm_in_background() -> None:
    import threading
    if os.environ.get("LINEAGE_NO_WARMUP") != "1":
        threading.Thread(target=warm_caches, daemon=True, name="warm-caches").start()


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


OWNERSHIP_VIEWS_PATH = Path("snapshot/ownership_views.json")


def ownership_views(g: Graph, months: int) -> dict:
    """Orphaned, dormant and gap views from ONE whole-tree scan (about 10 s), cached for 5 minutes."""
    def build() -> dict:
        from lineage.graph import ownership_views as ov

        stored = ov.load_precomputed(months, OWNERSHIP_VIEWS_PATH)  # computed once from the same snapshot: instant, instead of 10 s here and 90 s on a free web host
        return stored if stored is not None else ov.compute(g, months)  # otherwise live, with the activity window ending at the snapshot date
    return cached(("views", g.name, months), build)


def warm_caches() -> None:
    """Fill the slow whole-tree ownership caches in the background so the first visitor to Insights does not wait ~10 s."""
    try:
        g = _graph()
        for months in (12,):
            ownership_views(g, months)
        cached(("keyrev", g.name), lambda: review_network_top(g, 12))  # the PageRank run behind Insights
        _log.info("ownership caches warmed")
    except Exception as e:  # the app must still start if the graph is not ready yet
        _log.warning("cache warm-up skipped: %s", e)


def get_llm() -> LLM:
    try:
        return build_llm()
    except LLMNotConfigured as e:
        raise HTTPException(503, str(e))


class AskLimiter:
    """Protects the free-tier LLM quota on a public site: at most N /ask calls per visitor per hour and M per day for everyone.

    In-memory and per process (one container), which is enough for a demo; behind a proxy the visitor is the first X-Forwarded-For address.
    """

    def __init__(self, per_ip_hour: int, per_day: int, clock=None):
        import time
        from collections import deque
        self.per_ip_hour, self.per_day, self._clock = per_ip_hour, per_day, clock or time.time
        self._ip: dict[str, "deque[float]"] = {}
        self._all: "deque[float]" = deque()
        self._deque = deque

    def check(self, ip: str) -> None:
        now = self._clock()
        while self._all and now - self._all[0] > 86400:
            self._all.popleft()
        hits = self._ip.setdefault(ip, self._deque())
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if self.per_day and len(self._all) >= self.per_day:
            raise HTTPException(429, "The public demo has used its daily budget of questions. Findings, Explore and the graph still work; chat resets tomorrow.", headers={"Retry-After": "3600"})
        if self.per_ip_hour and len(hits) >= self.per_ip_hour:
            raise HTTPException(429, f"Limit of {self.per_ip_hour} questions per hour reached for this visitor.", headers={"Retry-After": str(int(3600 - (now - hits[0])))})
        hits.append(now)
        self._all.append(now)


ASK_LIMITER = AskLimiter(int(os.environ.get("ASK_PER_IP_PER_HOUR", "12")), int(os.environ.get("ASK_PER_DAY", "150")))


def ask_limit(request: Request) -> None:
    fwd = request.headers.get("x-forwarded-for", "")
    ASK_LIMITER.check(fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown"))


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
    from lineage.agent.local_embeddings import available

    return Health(status="ok" if db else "degraded", database=db, graph=g.name, llm_configured=llm_ok, detail=detail, embeddings=available())


ANSWER_CACHE = AnswerCache()


_EMBEDDER = None


def embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from lineage.agent.local_embeddings import LocalEmbedder

        _EMBEDDER = LocalEmbedder()
    return _EMBEDDER


_TFIDF = None


def get_retriever(g: Graph = Depends(get_graph)):
    """The plain-retrieval baseline's retriever (overridable in tests): FalkorDB's vector index with the local embedding model; in the slim build, keyword (TF-IDF) retrieval over the same passages."""
    from lineage.agent.local_embeddings import available

    if available():
        from lineage.search.vector import FalkorRetriever

        return FalkorRetriever(g, embedder())
    global _TFIDF
    if _TFIDF is None:
        from lineage.search.corpus import build_corpus
        from lineage.search.tfidf import Retriever

        _TFIDF = Retriever(build_corpus())
    return _TFIDF


RECENT_ANSWERS: "OrderedDict[str, dict]" = OrderedDict()  # answer_id -> {session, question, response}: what a note may be saved from (newest 300)


def remember_answer(resp: AskResponse, question: str, session_id: str | None) -> AskResponse:
    """Give a graph answer a server-side id; it can be saved as a note only if the server produced it and the validator accepted it."""
    if resp.mode != "graph" or not session_id:
        return resp
    from lineage.memory import can_save

    resp.answer_id = uuid.uuid4().hex[:12]
    held = resp.model_dump(by_alias=True)  # by alias, so evidence keeps its "from" key
    resp.can_save = can_save(held)[0]
    RECENT_ANSWERS[resp.answer_id] = {"session": session_id, "question": question, "response": held}
    while len(RECENT_ANSWERS) > 300:
        RECENT_ANSWERS.popitem(last=False)
    return resp


def memory_block(g: Graph, question: str, session_id: str | None) -> list[dict]:
    """This session's notes about entities the question mentions, each re-verified: the tools the note rested on are re-run NOW (no model) and the evidence compared."""
    from lineage import memory

    if not session_id:
        return []
    notes = memory.recall(g, memory.entities_in_text(g, question), session_id)
    tools = ToolBox(g)
    out = []
    for n in notes:
        sources = list(dict.fromkeys(r[3] for r in n["evidence_sig"] if r[3]))[:6]
        out.append({"id": n["id"], "text": n["text"], "learned_at": n["at"], "question": n["question"], "evidence_ids": n["evidence_ids"], "sources": sources, "reverified": memory.reverify(tools, n)})
    return out


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, request: Request, g: Graph = Depends(get_graph)) -> AskResponse:
    memory = memory_block(g, req.question, req.session_id) if req.mode == "graph" else []
    try:
        resp = _ask(req, request, g)
    except HTTPException as e:
        if memory and e.status_code in (429, 503):  # the model is unavailable or rate limited: a re-verified note can still answer
            return AskResponse(answer=memory[0]["text"], status="ok", citations=[], evidence=[], path=[], tool_trace=[], session_id=req.session_id, outcome="answered_from_memory", memory=memory)
        raise
    if memory and resp.outcome in ("answered_with_path", "not_in_graph"):
        resp.memory, resp.outcome = memory, "answered_from_memory"  # the note is shown first, with the date it was learned and whether the graph still agrees; the fresh answer follows
    return remember_answer(resp, req.question, req.session_id)


@app.post("/notes")
def save_note_endpoint(body: SaveNoteRequest, g: Graph = Depends(get_graph)) -> dict:
    """Save a validated graph answer (identified by its server-side id) as a note linked to the entities in its evidence. Private to the session."""
    from lineage import memory

    held = RECENT_ANSWERS.get(body.answer_id)
    if not held or held["session"] != body.session_id:
        raise HTTPException(404, "No such answer in this session (answers can be saved for a while after they are given).")
    try:
        return memory.save_note(g, held["response"], held["question"], body.session_id)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/notes")
def list_notes_endpoint(session_id: str = Query(..., min_length=6, max_length=64), g: Graph = Depends(get_graph)) -> list[dict]:
    from lineage import memory

    return memory.list_notes(g, session_id)


@app.delete("/notes/{note_id}")
def forget_note_endpoint(note_id: str, session_id: str = Query(..., min_length=6, max_length=64), g: Graph = Depends(get_graph)) -> dict:
    from lineage import memory

    if not memory.forget(g, note_id, session_id):
        raise HTTPException(404, "No such note in this session.")
    return {"forgotten": note_id}


def _ask(req: AskRequest, request: Request, g: Graph) -> AskResponse:
    # Saved answers for the demo questions come first: they need no LLM key, cost no quota and are not rate limited.
    saved = ANSWER_CACHE.get(req.question, req.mode)
    if saved:
        if req.session_id and req.mode == "graph":
            SESSIONS[req.session_id] += [{"role": "user", "content": req.question}, {"role": "assistant", "content": saved["answer"]}]
        return AskResponse(**{**saved, "session_id": req.session_id})
    early = preflight_result(g, req.question) if req.mode == "graph" else None
    if early:  # unknown release / mistyped gate in the question: answered in code, so it needs no model, no key and no rate-limit budget
        return AskResponse(answer=early.answer, status=early.status, citations=[], evidence=[], path=[], tool_trace=early.tool_trace, session_id=req.session_id, outcome=early.outcome)
    ask_limit(request)
    llm = request.app.dependency_overrides.get(get_llm, get_llm)()  # same override hook as before, resolved only when the model is really needed
    if req.mode == "retrieval":  # the contrast: same question, same model, same text, no graph
        from lineage.search.rag import answer_with_retrieval

        try:
            override = request.app.dependency_overrides.get(get_retriever)
            retriever = override() if override else get_retriever(g)
            return AskResponse(**answer_with_retrieval(llm, retriever, req.question), session_id=req.session_id)
        except ProviderError as e:
            raise HTTPException(503, f"The language model is unavailable right now ({str(e)[:160]}). Findings and the graph still work.")
    history = SESSIONS[req.session_id][-2 * MAX_TURNS:] if req.session_id else []
    try:
        res = run_agent(llm, ToolBox(g), req.question, history)
    except ProviderError as e:  # every provider failed after retries: say so plainly instead of a 500
        raise HTTPException(503, f"The language model is unavailable right now ({str(e)[:160]}). Findings and the graph still work.")
    if req.session_id:
        SESSIONS[req.session_id] += [{"role": "user", "content": req.question}, {"role": "assistant", "content": res.answer}]
    return AskResponse(answer=res.answer, status=res.status, citations=res.citations, evidence=res.evidence, path=res.path,
                       tool_trace=res.tool_trace, unsupported_citations=res.unsupported_citations, session_id=req.session_id, outcome=res.outcome, validation=res.validation)


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


_CACHE: dict = {}


def cached(key: tuple, build, ttl: int = 300):
    """Tiny in-process cache for the slow whole-tree ownership comparisons (about 10 s uncached)."""
    import time
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = build()
    _CACHE[key] = (time.time(), value)
    return value


@app.get("/orphans", response_model=list[OrphanRow])
def orphans(months: int = Query(12, ge=1, le=36), g: Graph = Depends(get_graph)) -> list[OrphanRow]:
    """Code folders whose listed approvers have all been inactive for `months` (sig-storage, sig-node and sig-apps PR data only). Cached for 5 minutes."""
    rows = ownership_views(g, months)["orphans"]
    return [OrphanRow(path=o["path"], listed_approvers=o["listed_approvers"], active_people=o["active_people"], active_but_not_listed=o["unlisted_active"]) for o in rows]


@app.get("/insights", response_model=InsightsResponse)
def insights(months: int = Query(12, ge=1, le=36), g: Graph = Depends(get_graph)) -> InsightsResponse:
    """Official-versus-actual ownership across all covered folders, components with no active owner, and the most central reviewers (FalkorDB PageRank). Cached for 5 minutes."""
    views = ownership_views(g, months)
    orphan_rows, gaps = views["orphans"], views["gaps"]
    return InsightsResponse(
        months=months, ownership_gaps=[GapRow(**r) for r in gaps],
        orphaned=[OrphanRow(path=o["path"], listed_approvers=o["listed_approvers"], active_people=o["active_people"], active_but_not_listed=o["unlisted_active"]) for o in orphan_rows],
        dormant=[DormantRow(path=d["path"], listed_approvers=d["listed_approvers"]) for d in views["dormant"]],
        key_reviewers=[KeyReviewer(**r) for r in cached(("keyrev", g.name), lambda: review_network_top(g, 12))],
        notes=[COVERAGE_NOTE, "PageRank ranks position in the review network (rank flows from an author to the people who review them); it is not a measure of skill or ownership.",
               "Orphaned = other people did work in the folder but no listed approver was active (the risk). Dormant = no recorded activity at all, often just stable code (not a risk).",
               "The activity window ends at the snapshot date (2026-10-15), where the pull-request data ends, not at today's date."])


@app.get("/schema", response_model=SchemaResponse)
def schema(g: Graph = Depends(get_graph)) -> SchemaResponse:
    """The whole graph at the level of types: how many nodes of each kind and how many relationships of each kind run between which kinds. Private session notes are left out."""
    def build() -> SchemaResponse:
        nodes = [SchemaNode(label=r[0], count=r[1]) for r in g.ro_query("MATCH (n) RETURN labels(n)[0], count(n) ORDER BY count(n) DESC").result_set if r[0] != "Note"]
        edges = [SchemaEdge(source=r[0], type=r[1], target=r[2], count=r[3]) for r in g.ro_query(
            "MATCH (a)-[r]->(b) WHERE ID(r) >= 0 RETURN labels(a)[0], type(r), labels(b)[0], count(r) ORDER BY count(r) DESC").result_set if "Note" not in (r[0], r[2])]
        return SchemaResponse(nodes=nodes, edges=edges)
    return cached(("schema", g.name), build, ttl=3600)


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


def _current_digest(g: Graph) -> dict:
    from lineage.changes import build_digest, findings_from_graph

    manifest = json.loads(MANIFEST.read_text())
    return cached(("digest", g.name), lambda: build_digest(Path("snapshot"), findings_from_graph(g), manifest["snapshot_date"],
                                                           [{"repo": r["repo"], "commit": r["commit"]} for r in manifest["sources"]], SAMPLED_RELEASES[-1]))  # live snapshot: ownership at HEAD


def _info(digest: dict, current: bool) -> SnapshotInfo:
    from datetime import date

    age = (date.today() - date.fromisoformat(digest["snapshot_date"])).days
    return SnapshotInfo(id=digest["snapshot_date"], snapshot_date=digest["snapshot_date"], age_days=max(age, 0), sources=digest["sources"], is_current=current)


@app.get("/search", response_model=SearchResponse)
def search(q: str = Query(..., min_length=3, max_length=300), k: int = Query(5, ge=1, le=8), g: Graph = Depends(get_graph)) -> SearchResponse:
    """Text search without an LLM: the question is embedded locally and matched against the FalkorDB vector index, then each hit is expanded along the graph
    (same evidence bundle as the agent's search_text tool; passages less similar than the floor are not returned)."""
    from lineage.agent.local_embeddings import SLIM_NOTE, available

    if not available():
        return SearchResponse(query=q, status="unavailable", evidence=[], path=[], note="Text search " + SLIM_NOTE + ".")
    b = ToolBox(g, embedder=embedder()).search_text(q, k)
    return SearchResponse(query=q, status=b["status"], evidence=b["evidence"], path=b["path"], note=b["params"].get("note"))


@app.get("/snapshots", response_model=SnapshotList)
def snapshots(g: Graph = Depends(get_graph)) -> SnapshotList:
    """The live snapshot and the earlier ones kept in snapshot/history/ (digests built by running the same pipeline on the repos at that date)."""
    from lineage.changes import load_history

    cur = _current_digest(g)
    return SnapshotList(current=_info(cur, True), previous=[_info(d, False) for k, d in sorted(load_history().items(), reverse=True) if k < cur["snapshot_date"]])


@app.get("/changes-since", response_model=ChangesSince, response_model_by_alias=True)
def changes_since(since: str = Query("latest", description="a snapshot date from /snapshots, or 'latest' for the newest earlier snapshot"), g: Graph = Depends(get_graph)) -> ChangesSince:
    """What changed between an earlier snapshot and the live one: findings that are new, resolved, moved or still open (with how long they have been open),
    feature-gate stage changes, edited docs labels, design-document status changes and ownership roles gained or lost."""
    from lineage.changes import diff_digests, load_history

    cur = _current_digest(g)
    history = {k: d for k, d in load_history().items() if k < cur["snapshot_date"]}
    if not history:
        raise HTTPException(404, "No earlier snapshot is stored (snapshot/history/ is empty), so there is nothing to compare with.")
    key = max(history) if since == "latest" else since
    if key not in history:
        raise HTTPException(404, f"No stored snapshot for {since!r}. Available: {', '.join(sorted(history))}.")
    out = diff_digests(history[key], cur)
    firsts = {}
    for k, d in sorted(load_history().items()):  # oldest first: when did each still-open finding first appear?
        for fk in d["findings"]:
            firsts.setdefault(fk, d["snapshot_date"])
    for f in out["findings"]["persisting"]:
        f["first_seen"] = firsts.get(f["key"], cur["snapshot_date"])
    out["caveats"] = ["Earlier snapshots are rebuilt by running the same pipeline on the repos as they were on that date; the first_seen date is the earliest stored snapshot that has the finding, not the day it appeared.",
                      "A section that moves to another page is reported as 'moved', never as fixed.",
                      "Ownership is sampled at release tags, so role changes are reported between sampled releases."]
    return ChangesSince.model_validate(out)


@app.get("/about-data", response_model=AboutData)
def about_data(g: Graph = Depends(get_graph)) -> AboutData:
    from lineage.detect.drift import run_detector
    from lineage.graph.loader import counts

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    _, stats = run_detector(g)
    manual = g.ro_query("MATCH ()-[a:ABOUT {method: 'manual'}]->() RETURN count(a)").result_set[0][0]
    from datetime import date

    age = (date.today() - date.fromisoformat(manifest["snapshot_date"])).days if manifest.get("snapshot_date") else None
    return AboutData(snapshot_date=manifest.get("snapshot_date"), age_days=age, sources=manifest.get("sources", []), counts=counts(g), detector=stats,
                     manual_links=manual, limitations=[
                         "Hard-coded labels are linked to gates by inference; only anchored links (the gate is named in the section, or the since-version rule left one clear winner) or hand-verified links can produce a finding, the rest are 'possible drift' for a human to confirm.",
                         "Many docs labels describe features that never had a feature gate, so they cannot be checked.",
                         "Ownership history is sampled at release tags 1.30-1.37 and HEAD; docs (website) ownership is HEAD only.",
                         "Pull-request data covers merged PRs labelled sig/storage, sig/node or sig/apps from the last two years; folders owned by other teams show no activity, so 'who actually reviews' is a floor, not a census.",
                         "Lineage only drafts. It never posts to Kubernetes.",
                     ])
