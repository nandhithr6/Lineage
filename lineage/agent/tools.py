"""Tier-1 tools: curated, parameterised, READ-ONLY Cypher behind a small interface.

Each tool returns an evidence bundle (CLAUDE.md §8): {tool, params, evidence[], path[], status}. Evidence items carry
the source_url that proves them; provenance comes from here, never from the LLM. status: ok | empty | insufficient_evidence.
"""
from __future__ import annotations

import time

from falkordb import Graph

from lineage.agent.resolve import RESOLVERS, Resolution, resolve_entity, resolve_gate_or_path, resolve_path
from lineage.graph.queries import (LATEST, SAMPLED_RELEASES, effective_owners, experts_for, gate_stage_change, official_vs_actual, orphaned_components, owners_diff, ownership_gaps,
                                   review_network_top)
from lineage.graph.trace import PR_STRONG, Q_HISTORY, Q_KEP_PR_COUNTS, Q_KEP_PRS, Q_KEPS, SEVERITY_ORDER, build_trace, rank_keps, stage_ranges

MAX_EVIDENCE = 40


def bundle(tool: str, params: dict) -> dict:
    return {"tool": tool, "params": params, "evidence": [], "path": [], "status": "empty"}


def put(b: dict, kind: str, type_: str, frm: str, to: str | None, props: dict, url: str | None) -> None:
    if len(b["evidence"]) < MAX_EVIDENCE:
        b["evidence"].append({"id": f"e{len(b['evidence']) + 1}", "kind": kind, "type": type_, "from": frm, "to": to,
                              "props": props, "source_url": url})
        b["status"] = "ok"


def clean_path(path: str) -> str:
    """Models pass 'OWNERS', '/', '.', 'pkg/volume/OWNERS/' for folders: all mean the folder itself (and '' is the repository root)."""
    p = (path or "").strip().strip("/")
    if p in (".", "OWNERS", "OWNERS_ALIASES"):
        return ""
    return p[: -len("/OWNERS")] if p.endswith("/OWNERS") else p


def release_num(as_of: str | int | None) -> int:
    if as_of in (None, "", "latest", "now", "current"):
        return LATEST
    s = str(as_of).lstrip("v")
    major, minor = (s.split(".") + ["0"])[:2]
    return int(major) * 100 + int(minor)


def ownership_release_problem(num: int, repo: str) -> str | None:
    """Why ownership cannot be answered "as of" this release, or None. Anything outside the sampled releases must be refused, never answered with the latest data:
    a release in the future (or before 1.30) has no record, and silently returning today's owners would be a false statement of fact."""
    if repo != "kubernetes" and num != LATEST:
        return ("the ownership history of the website (docs) repository holds only the latest commit, so there is no record of who owned it in an earlier release")
    if num > LATEST:
        return (f"release 1.{num % 100} is newer than the data: ownership is recorded at releases 1.30-1.37 and at the latest commit (1.38-dev), "
                "and the future cannot be known")
    if num not in SAMPLED_RELEASES:
        return "ownership history is available for kubernetes/kubernetes at releases 1.30-1.37 and latest only"
    return None


SEARCH_FLOOR = 0.55  # cosine similarity below which a chunk is not evidence. Calibrated on 2026-10-16: off-topic questions top out at 0.48, on-topic ones start at 0.65 (DECISIONS.md)


class ToolBox:
    def __init__(self, g: Graph, embedder=None):
        self.g = g
        self._embedder = embedder  # local bge-base model, created on first use (search_text only)

    @property
    def embedder(self):
        if self._embedder is None:
            from lineage.agent.local_embeddings import LocalEmbedder

            self._embedder = LocalEmbedder()
        return self._embedder

    # -- tools ------------------------------------------------------------------------------------------
    def drift_findings(self, severity: str | None = None, sig: str | None = None, limit: int = 20) -> dict:
        """List active drift findings and review items, most severe first. severity: finding | minor | review."""
        b = bundle("drift_findings", {"severity": severity, "sig": sig, "limit": limit})
        rows = self.g.ro_query("""
            MATCH (f:Finding {active: true})-[:FOR_CLAIM]->(c:Claim)<-[:HAS_CLAIM]-(s:DocSection)<-[:HAS_SECTION]-(p:DocPage),
                  (f)-[:FOR_GATE]->(g:FeatureGate)
            OPTIONAL MATCH (k:KEP)-[:INTRODUCES]->(g), (k)-[:OWNED_BY]->(sig:SIG)
            RETURN f.key, f.kind, f.severity, f.message, c.source_url, p.title, p.url, s.heading, g.name, collect(DISTINCT sig.id)
        """).result_set
        rows = [r for r in rows if (not severity or r[2] == severity) and (not sig or sig in r[9])]
        rows.sort(key=lambda r: (SEVERITY_ORDER.get(r[2], 9), r[5] or "", r[7] or ""))
        for key, kind, sev, msg, url, title, page_url, heading, gate, sigs in rows[:limit]:
            put(b, "node", "Finding", f"DocPage:{title}", f"FeatureGate:{gate}",
                {"key": key, "kind": kind, "severity": sev, "message": msg, "page": page_url, "section": heading, "teams": sigs}, url)
        b["params"]["total_matching"] = len(rows)
        return b

    def explain_finding(self, query: str) -> dict:
        """Full trace for one finding: claim, link, gate history, KEP, team, suggested reviewers. query = part of the page path or a finding key."""
        t = build_trace(self.g, query)
        out = {"tool": "explain_finding", "params": {"query": query}, "evidence": t["evidence"], "path": t["path"], "status": t["status"]}
        if t["status"] != "ok":
            out["evidence"], out["status"] = [], "insufficient_evidence"
        return out

    def suggest_reviewers(self, query: str) -> dict:
        """Who should review the fix for a finding, and why: docs approvers who can merge it, domain experts who can check the facts, each reason with its source. query = part of the page path or a finding key."""
        t = build_trace(self.g, query)
        b = bundle("suggest_reviewers", {"query": query})
        if t["status"] != "ok":
            b["status"] = "insufficient_evidence"
            return b
        for r in (t["sections"].get("reviewers") or [])[:8]:
            for x in r["reasons"][:3]:
                put(b, "edge", "SUGGESTED_REVIEWER", f"Person:{r['handle']}", f"Finding:{t['sections']['finding']['key']}",
                    {"group": r["group"], "score": r["score"], "category": x["category"], "reason": x["reason"]}, x["source_url"])
        if b["status"] == "ok":
            b["path"] = [f"Finding:{t['sections']['finding']['key']}"]
        return b

    def gate_timeline(self, gate: str) -> dict:
        """Stage history of a feature gate (alpha/beta/stable/deprecated ranges, whether it was removed)."""
        b = bundle("gate_timeline", {"gate": gate})
        info = self.g.ro_query("MATCH (g:FeatureGate {name: $g}) RETURN g.removed, g.final_stage, g.source_url", {"g": gate}).result_set
        if not info:
            b["status"] = "insufficient_evidence"
            return b
        removed, final, url = info[0]
        put(b, "node", "FeatureGate", f"FeatureGate:{gate}", None, {"removed": bool(removed), "final_stage": final}, url)
        for h in stage_ranges(self.g.ro_query(Q_HISTORY, {"g": gate}).result_set):
            put(b, "edge", "STAGE_IN", f"FeatureGate:{gate}", f"Release:{h['from']}..{h['to']}",
                {"stage": h["stage"], "from": h["from"], "to": h["to"], "overlap": h["overlap"]}, h["source_url"])
        b["path"] = [f"FeatureGate:{gate}"] + [f"stage:{h['stage']}" for h in stage_ranges(self.g.ro_query(Q_HISTORY, {"g": gate}).result_set)]
        return b

    def doc_claims(self, page_or_section: str, limit: int = 25) -> dict:
        """Maturity labels on a docs page (match on page path, page title or section heading, any case) with the candidate feature gates each is linked to and how confident each link is."""
        b = bundle("doc_claims", {"page_or_section": page_or_section})
        rows = self.g.ro_query("""
            MATCH (p:DocPage)-[:HAS_SECTION]->(s:DocSection)-[:HAS_CLAIM]->(c:Claim)
            WHERE toLower(p.path) CONTAINS toLower($q) OR toLower(p.title) CONTAINS toLower($q) OR toLower(s.heading) CONTAINS toLower($q)
            OPTIONAL MATCH (c)-[a:ABOUT]->(g:FeatureGate)
            RETURN c.id, c.kind, c.state, c.since, c.line, c.source_url, c.drift_eligible, s.heading, p.path, g.name, a.link_confidence, a.method
        """, {"q": page_or_section}).result_set
        claims: dict[str, dict] = {}
        for r in rows:
            c = claims.setdefault(r[0], {"row": r, "cands": []})
            if r[9]:
                c["cands"].append({"gate": r[9], "confidence": r[10], "method": r[11]})
        for cid, c in sorted(claims.items(), key=lambda kv: (kv[1]["row"][8], kv[1]["row"][4]))[:limit]:
            _, kind, state, since, line, url, eligible, heading, path, *_ = c["row"]
            cands = sorted(c["cands"], key=lambda x: -(x["confidence"] or 0))[:3]
            top = cands[0] if cands else None
            put(b, "node", "Claim", f"DocPage:{path}", f"FeatureGate:{top['gate']}" if top else None,
                {"kind": kind, "stage": state, "since": since, "line": line, "section": heading, "linked_gate": top["gate"] if top else None,
                 "link_confidence": top["confidence"] if top else None, "link_method": top["method"] if top else None,
                 "candidate_gates": cands, "link_note": ("confident" if top and (top["confidence"] or 0) >= 0.8 else "NOT confident: a possible link only" if top else "no gate linked"),
                 "checked_for_drift": bool(eligible)}, url)
        return b

    def owners_of(self, path: str, repo: str = "kubernetes", as_of: str | None = None) -> dict:
        """Who approves/reviews a folder or file (inheritance and teams resolved). repo: kubernetes | website. as_of: release like 1.30 (kubernetes history 1.30+)."""
        num = release_num(as_of)
        path = clean_path(path)
        is_file = path.endswith((".md", ".go", ".yaml", ".py"))
        b = bundle("owners_of", {"path": path, "repo": repo, "as_of": as_of or "latest"})
        problem = ownership_release_problem(num, repo)
        if problem:  # say WHY there is no answer, so the model can stop instead of searching on
            b["status"] = "insufficient_evidence"
            b["params"]["note"] = problem + ". Do not substitute the current owners for the requested release."
            return b
        rows = effective_owners(self.g, repo, path, num, is_file=is_file)
        seen: set[tuple] = set()
        for r in rows:
            if r["role"] == "REVIEWER_OF" and len(b["evidence"]) >= 25:
                continue
            k = (r["handle"], r["role"], r["declared_at"])
            if k in seen:
                continue
            seen.add(k)
            put(b, "edge", r["role"], f"Person:{r['handle']}", f"CodePath:{repo}:{r['declared_at']}",
                {"via_team": r["via_team"], "valid_from_release": r["valid_from"], "valid_to_release": r["valid_to"],
                 "hops_above_path": r["hops"], "as_of": b["params"]["as_of"]}, r["source_url"])
        if b["status"] == "ok":
            b["path"] = [f"CodePath:{repo}:{path}"] + sorted({f"CodePath:{repo}:{r['declared_at']}" for r in rows})
        return b

    def decision_lineage(self, kep_or_gate: str) -> dict:
        """The design decision (KEP) behind a feature gate or topic: status, owning team, approvers, replaced/related KEPs."""
        b = bundle("decision_lineage", {"kep_or_gate": kep_or_gate})
        rows = self.g.ro_query(Q_KEPS, {"g": kep_or_gate}).result_set
        keps = rank_keps(rows, kep_or_gate) if rows else []
        if not keps:  # not a gate name: match KEP ids/titles
            found = self.g.ro_query("""MATCH (k:KEP) WHERE toLower(k.id) CONTAINS toLower($q) OR toLower(k.title) CONTAINS toLower($q)
                OPTIONAL MATCH (k)-[:OWNED_BY]->(s:SIG) RETURN k.id, k.title, k.status, k.stage, k.latest_milestone, k.source_url, s.id, s.name LIMIT 5""",
                                   {"q": kep_or_gate}).result_set
            keps = [{"id": r[0], "title": r[1], "status": r[2], "stage": r[3], "milestone": r[4], "source_url": r[5], "method": "title_or_id_match",
                     "confidence": None, "introduces_url": r[5], "sig": r[6], "sig_name": r[7]} for r in found]
        for k in keps[:5]:
            put(b, "node", "KEP", f"KEP:{k['id']}", None, {"title": k["title"], "status": k["status"], "stage": k["stage"],
                                                          "milestone": k["milestone"], "match": k["method"], "match_confidence": k["confidence"]}, k["source_url"])
            if k["sig"]:
                put(b, "edge", "OWNED_BY", f"KEP:{k['id']}", f"SIG:{k['sig']}", {"team": k["sig_name"]}, k["source_url"])
            for (h,) in self.g.ro_query("MATCH (p:Person)-[:AUTHORED]->(k:KEP {id: $k}) RETURN p.handle", {"k": k["id"]}).result_set[:6]:
                put(b, "edge", "AUTHORED", f"Person:{h}", f"KEP:{k['id']}", {}, k["source_url"])
            for (h,) in self.g.ro_query("MATCH (p:Person)-[:APPROVED_KEP]->(k:KEP {id: $k}) RETURN p.handle", {"k": k["id"]}).result_set[:6]:
                put(b, "edge", "APPROVED_KEP", f"Person:{h}", f"KEP:{k['id']}", {}, k["source_url"])
            for rel, other, url in self.g.ro_query("""MATCH (k:KEP {id: $k})-[r:REPLACES|SEE_ALSO|SUPERSEDED_BY]->(o:KEP) RETURN type(r), o.id, r.source_url LIMIT 6""",
                                                   {"k": k["id"]}).result_set:
                put(b, "edge", rel, f"KEP:{k['id']}", f"KEP:{other}", {}, url)
            if k is keps[0]:  # pull requests that implement the primary decision, and the tickets they close (inferred links: method + confidence shown)
                strong, weak = (self.g.ro_query(Q_KEP_PR_COUNTS, {"k": k["id"], "min": PR_STRONG}).result_set or [[0, 0]])[0]
                for num, title, merged, url, method, conf, ev, issues in self.g.ro_query(Q_KEP_PRS, {"k": k["id"], "min": PR_STRONG, "limit": 4}).result_set:
                    put(b, "edge", "IMPLEMENTS", f"PR:{num}", f"KEP:{k['id']}", {"title": title, "merged_at": merged, "method": method, "confidence": conf, "evidence": ev,
                                                                                  "note": f"{strong} PRs link to this KEP at confidence >= {PR_STRONG}, {weak} more only mention its feature gate"}, url)
                    for i in [i for i in issues if i and i[0]][:2]:
                        put(b, "edge", "FIXES", f"PR:{num}", f"Issue:{i[0]}", {"title": i[1], "state": i[2]}, i[3])
        if keps:
            b["path"] = [f"FeatureGate:{kep_or_gate}" if rows else f"query:{kep_or_gate}", f"KEP:{keps[0]['id']}"] + ([f"SIG:{keps[0]['sig']}"] if keps[0]["sig"] else [])
        return b

    def what_changed(self, entity: str, from_release: str, to_release: str, repo: str = "kubernetes") -> dict:
        """What changed between two releases: for a feature gate, its stage; for a code/docs path, who gained or lost an approver/reviewer role. Ownership history is sampled at releases 1.30-1.37 and now."""
        a, b = release_num(from_release), release_num(to_release)
        bndl = bundle("what_changed", {"entity": entity, "from_release": from_release, "to_release": to_release, "repo": repo})
        if self.g.ro_query("MATCH (g:FeatureGate {name: $g}) RETURN g.source_url", {"g": entity}).result_set:
            newest = self.g.ro_query("MATCH (r:Release) RETURN max(r.num)").result_set[0][0]
            future = [n for n in (a, b) if n > max(newest, LATEST)]
            if future:
                bndl["status"] = "insufficient_evidence"
                bndl["params"]["note"] = f"release 1.{future[0] % 100} is newer than the data (newest release recorded: 1.{newest % 100}); the future cannot be known"
                return bndl
            c = gate_stage_change(self.g, entity, a, b)
            put(bndl, "edge", "STAGE_CHANGE", f"FeatureGate:{entity}", None,
                {"from_release": from_release, "to_release": to_release, "stage_then": c["from"] or "none", "stage_now": c["to"] or "none",
                 "changed": c["from"] != c["to"]}, c["source_url"])
            bndl["path"] = [f"FeatureGate:{entity}"]
            return bndl
        problem = next((p for p in (ownership_release_problem(a, repo), ownership_release_problem(b, repo)) if p), None)
        if problem:
            bndl["status"] = "insufficient_evidence"
            bndl["params"]["note"] = problem
            return bndl
        d = owners_diff(self.g, repo, entity.strip("/"), a, b, is_file=entity.endswith((".md", ".go", ".yaml", ".py")))
        for kind, rows in (("OWNER_ADDED", d["added"]), ("OWNER_REMOVED", d["removed"])):
            for r in rows[:15]:
                put(bndl, "edge", kind, f"Person:{r['handle']}", f"CodePath:{repo}:{r['declared_at']}",
                    {"role": r["role"], "via_team": r["via_team"], "from_release": from_release, "to_release": to_release}, r["source_url"])
        if bndl["status"] == "empty":  # no change is a real, citable answer when the path exists
            has = self.g.ro_query("MATCH (c:CodePath {id: $i}) RETURN c.id", {"i": f"{repo}:{entity.strip('/')}"}).result_set
            if has:
                put(bndl, "node", "NoChange", f"CodePath:{repo}:{entity}", None,
                    {"from_release": from_release, "to_release": to_release, "unchanged_owner_roles": d["unchanged"], "added": 0, "removed": 0}, None)
        bndl["path"] = [f"CodePath:{repo}:{entity}"] if bndl["evidence"] else []
        return bndl

    COVERAGE = ("Only merged pull requests labelled sig/storage, sig/node or sig/apps from the last two years are loaded, so this is a floor, not a census; "
                "a path owned by another team has no activity data.")

    def experts_for(self, path: str, repo: str = "kubernetes", months: int = 12) -> dict:
        """Who actually reviews and approves changes under a code folder (recent activity weighted most), and how that compares with the people the OWNERS file lists."""
        p = path.strip("/")
        b = bundle("experts_for", {"path": p, "repo": repo, "months": months})
        ex = experts_for(self.g, repo, p)
        if not ex:
            b["status"] = "insufficient_evidence"
            b["params"]["note"] = self.COVERAGE
            return b
        v = official_vs_actual(self.g, repo, p, months)
        listed = set(v["active_approvers"]) | set(v["inactive_approvers"])
        for e in ex[:8]:
            put(b, "edge", "REVIEWS_ACTIVELY", f"Person:{e['handle']}", f"CodePath:{repo}:{p}",
                {"recency_weighted_score": e["score"], "prs_reviewed": e["prs_reviewed"], "approvals": e["approvals"], "last_active": e["last_active"],
                 "listed_approver": e["handle"] in listed, "example_pr": e["examples"][0]["url"]}, e["examples"][0]["url"])
        put(b, "node", "OfficialVsActual", f"CodePath:{repo}:{p}", None,
            {"listed_approvers": v["listed_approvers"], "listed_and_active": v["active_approvers"][:12], "listed_but_inactive_count": len(v["inactive_approvers"]),
             "listed_but_inactive": v["inactive_approvers"][:12], "active_but_not_listed": v["unlisted_active"][:12], "window_months": months, "coverage": self.COVERAGE}, None)
        b["path"] = [f"CodePath:{repo}:{p}"]
        return b

    def orphaned_components(self, months: int = 12) -> dict:
        """Code folders that list approvers, none of whom reviewed or authored anything touching the folder in the last N months."""
        b = bundle("orphaned_components", {"months": months})
        for o in orphaned_components(self.g, months)[:20]:
            put(b, "node", "OrphanedPath", f"CodePath:kubernetes:{o['path']}", None, {"listed_approvers": o["listed_approvers"], "active_people": o["active_people"],
                                                                                 "active_but_not_listed": o["unlisted_active"], "window_months": months, "coverage": self.COVERAGE}, None)
        if b["status"] == "empty":
            put(b, "node", "NoneFound", "CodePath:kubernetes", None, {"months": months, "coverage": self.COVERAGE}, None)
        put(b, "node", "Caveat", "Orphaned", None, {"note": "Orphaned means other people worked in the folder but no listed approver was active. Folders with no activity at all are dormant (often stable code) and are not listed here."}, None)
        return b

    def key_reviewers(self, limit: int = 10) -> dict:
        """The most central people in the code-review network (FalkorDB PageRank over author-to-reviewer edges) and whether OWNERS lists them as approvers."""
        b = bundle("key_reviewers", {"limit": limit})
        for r in review_network_top(self.g, min(max(int(limit), 1), 25)):
            put(b, "node", "ReviewNetworkRank", f"Person:{r['handle']}", None,
                {"rank": r["rank"], "pagerank": r["score"], "authors_relying_on_them": r["authors_served"], "reviews": r["reviews"],
                 "approver_of_folders_today": r["approver_of_folders"], "listed_approver": r["listed_approver"]}, None)
        if b["status"] == "ok":
            put(b, "node", "Caveat", "ReviewNetwork", None, {"note": "PageRank measures position in the review network among sig/storage, sig/node and sig/apps pull requests from the last two years "
                                                              "(backports and repo-wide PRs excluded). It is NOT a measure of skill or ownership."}, None)
        else:
            b["status"] = "insufficient_evidence"
        return b

    def ownership_gaps(self, months: int = 12, limit: int = 8) -> dict:
        """Code folders where OWNERS and reality disagree most: listed approvers with no activity in the last N months, or no active approver at all."""
        b = bundle("ownership_gaps", {"months": months, "limit": limit})
        for r in ownership_gaps(self.g, months, limit=min(max(int(limit), 1), 25)):
            put(b, "node", "OwnershipGap", f"CodePath:kubernetes:{r['path']}", None, {k: v for k, v in r.items() if k != "path"} | {"window_months": months, "coverage": self.COVERAGE}, None)
        return b

    def search_text(self, query: str, k: int = 5) -> dict:
        """Free-text search over docs sections, feature gates, design documents, teams and OWNERS text (FalkorDB vector index), then expand each hit along the graph:
        a docs hit shows the feature gate it is confidently about, a gate hit shows the design document and team behind it, a KEP hit shows its owning team."""
        from lineage.search.vector import vector_search

        k = min(max(int(k), 1), 8)
        b = bundle("search_text", {"query": query, "k": k})
        hits = [h for h in vector_search(self.g, self.embedder.embed_query(query), k) if h["similarity"] >= SEARCH_FLOOR]
        for h in hits:
            lines = f"L{h['line_start']}-L{h['line_end']}" if h["line_start"] else None
            put(b, "node", "Chunk", f"Chunk:{h['id']}", h["parent"], {"similarity": h["similarity"], "kind": h["kind"], "lines": lines, "text": h["text"][:700]}, h["source_url"])
        seen: set[str] = set()
        for h in hits:
            parent = h["parent"]
            if not parent or parent in seen:
                continue
            seen.add(parent)
            label, _, key = parent.partition(":")
            if label == "DocSection":
                for gate, conf, cid, url in self.g.ro_query(
                        "MATCH (s:DocSection {id: $k})-[:HAS_CLAIM]->(c:Claim)-[a:ABOUT]->(g:FeatureGate) WHERE a.link_confidence >= 0.8 RETURN g.name, a.link_confidence, c.id, a.source_url",
                        {"k": key}).result_set:
                    put(b, "edge", "ABOUT", f"Claim:{cid}", f"FeatureGate:{gate}", {"link_confidence": conf, "note": "confident automatic link from a docs label to a feature gate"}, url)
            elif label == "FeatureGate":
                for kep, sig, url in self.g.ro_query(
                        "MATCH (k:KEP)-[i:INTRODUCES]->(g:FeatureGate {name: $k}) WHERE i.confidence >= 0.7 OPTIONAL MATCH (k)-[:OWNED_BY]->(s:SIG) RETURN k.id, s.id, i.source_url LIMIT 2",
                        {"k": key}).result_set:
                    put(b, "edge", "INTRODUCES", f"KEP:{kep}", f"FeatureGate:{key}", {"owning_team": sig}, url)
            elif label == "KEP":
                for sig, status, url in self.g.ro_query("MATCH (k:KEP {id: $k})-[o:OWNED_BY]->(s:SIG) RETURN s.id, k.status, o.source_url", {"k": key}).result_set:
                    put(b, "edge", "OWNED_BY", f"KEP:{key}", f"SIG:{sig}", {"kep_status": status}, url)
        if b["status"] == "ok":
            b["path"] = [h["parent"] for h in hits if h["parent"]][:6]
        elif not hits:
            b["status"] = "insufficient_evidence"
            b["params"]["note"] = "no stored passage is similar enough to the question"
        return b

    def path_between(self, a: str, b: str, max_hops: int = 4) -> dict:
        """How two things in the graph are connected: the cheapest path between them (FalkorDB algo.SPpaths over ownership, membership, design-decision and docs-structure links),
        returned hop by hop. a and b can each be a feature gate, person, team, SIG, KEP, folder or docs page."""
        from lineage.agent.resolve import resolve_entity
        from lineage.graph.paths import PathTimeout, describe_path, shortest_path

        b_ = bundle("path_between", {"a": a, "b": b, "max_hops": max_hops})
        ra, rb = resolve_entity(self.g, a), resolve_entity(self.g, b)
        if not (ra.ok and rb.ok):  # call() already refuses unknown names; direct callers get the same answer
            b_["status"] = "insufficient_evidence"
            return b_
        if (ra.label, ra.key) == (rb.label, rb.key):
            b_["params"]["note"] = "both names are the same entity"
            return b_
        try:
            p = shortest_path(self.g, (ra.label, ra.key), (rb.label, rb.key), max_hops)
        except PathTimeout as e:
            b_["status"] = "insufficient_evidence"
            b_["params"]["note"] = f"{e}; try fewer hops"
            return b_
        if not p:
            b_["params"]["note"] = f"both exist, but no path of up to {min(max_hops, 5)} hops connects them through ownership, membership, design decisions or docs structure"
            return b_
        for h in p["hops"]:
            put(b_, "edge", h["type"], h["from"], h["to"], {"direction": h["direction"], **h["props"]}, h["source_url"])
        b_["path"] = p["nodes"]
        b_["params"]["summary"] = describe_path(p)
        return b_

    def sig_leaders(self, sig: str) -> dict:
        """Chairs, tech leads and emeritus leads of a team (SIG, working group or committee) from the org chart."""
        b = bundle("sig_leaders", {"sig": sig})
        rows = self.g.ro_query("""MATCH (s:SIG) WHERE s.id = $s OR toLower(s.name) = toLower($s)
            OPTIONAL MATCH (p:Person)-[r:CHAIRS|TECH_LEAD_OF|EMERITUS_LEAD_OF]->(s)
            RETURN s.id, s.name, s.group_type, s.source_url, p.handle, p.name, type(r)""", {"s": sig}).result_set
        if not rows:
            b["status"] = "insufficient_evidence"
            return b
        sid, name, gtype, url = rows[0][:4]
        put(b, "node", "SIG", f"SIG:{sid}", None, {"name": name, "group_type": gtype}, url)
        role = {"CHAIRS": "chair", "TECH_LEAD_OF": "tech lead", "EMERITUS_LEAD_OF": "emeritus lead"}
        for _, _, _, _, handle, pname, rel in rows:
            if handle:
                put(b, "edge", rel, f"Person:{handle}", f"SIG:{sid}", {"role": role[rel], "name": pname}, url)
        b["path"] = [f"SIG:{sid}"]
        return b

    # -- schemas for the LLM ----------------------------------------------------------------------------
    SCHEMAS = [
        {"name": "drift_findings", "description": "List documentation labels that look out of date (findings) and items needing a human look (review). Use for 'which docs are stale'.",
         "parameters": {"type": "object", "properties": {"severity": {"type": "string", "enum": ["finding", "minor", "review"]},
                                                         "sig": {"type": "string", "description": "team id such as sig-storage"}, "limit": {"type": "integer"}}}},
        {"name": "explain_finding", "description": "Full evidence trace for one drift finding: the claim, the linked gate, gate history, design decision, team and suggested reviewers.",
         "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "part of the docs page path, e.g. resource-quotas"}}, "required": ["query"]}},
        {"name": "suggest_reviewers", "description": "Who should review the fix for a stale-docs finding and why: docs approvers (can merge) and domain experts (can check the facts), each reason with its source. query = part of the docs page path or a finding key.",
         "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
        {"name": "gate_timeline", "description": "Stage history (alpha/beta/stable/deprecated) of a Kubernetes feature gate by exact name.",
         "parameters": {"type": "object", "properties": {"gate": {"type": "string"}}, "required": ["gate"]}},
        {"name": "doc_claims", "description": "Maturity labels on a docs page or section, with the feature gate each is linked to and how confident the link is.",
         "parameters": {"type": "object", "properties": {"page_or_section": {"type": "string"}}, "required": ["page_or_section"]}},
        {"name": "owners_of", "description": "Who approves or reviews a code or docs path, with inheritance. Supports history for kubernetes/kubernetes from release 1.30.",
         "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "repo": {"type": "string", "enum": ["kubernetes", "website"]},
                                                         "as_of": {"type": "string", "description": "release such as 1.30; omit for now"}}, "required": ["path"]}},
        {"name": "what_changed", "description": "What changed between two releases (sampled 1.30 to now): for a feature gate its maturity stage, for a code or docs path who gained or lost an approver or reviewer role.",
         "parameters": {"type": "object", "properties": {"entity": {"type": "string", "description": "a feature gate name or a path such as pkg/controller/job"},
                                                         "from_release": {"type": "string"}, "to_release": {"type": "string", "description": "e.g. 1.37 or latest"},
                                                         "repo": {"type": "string", "enum": ["kubernetes", "website"]}}, "required": ["entity", "from_release", "to_release"]}},
        {"name": "experts_for", "description": "Who ACTUALLY reviews and approves changes under a code folder (recent activity weighted most) versus who the OWNERS file lists. Covers only sig-storage, sig-node and sig-apps PRs from the last two years.",
         "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "months": {"type": "integer"}}, "required": ["path"]}},
        {"name": "orphaned_components", "description": "Code folders whose listed approvers have all been inactive for the given number of months (default 12). Covers only sig-storage, sig-node and sig-apps.",
         "parameters": {"type": "object", "properties": {"months": {"type": "integer"}}}},
        {"name": "key_reviewers", "description": "Who is most central in the code-review network (graph PageRank) and whether they are listed approvers. Covers sig-storage, sig-node, sig-apps PRs from the last two years.",
         "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
        {"name": "ownership_gaps", "description": "Code folders where the OWNERS file and real review activity disagree most (listed approvers who went quiet).",
         "parameters": {"type": "object", "properties": {"months": {"type": "integer"}, "limit": {"type": "integer"}}}},
        {"name": "search_text", "description": "Free-text search when no other tool fits: finds the most similar passages (docs, feature gates, design documents, teams, OWNERS) and the graph facts around them. Use the specific tools first for owners, stages and findings.",
         "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}},
        {"name": "path_between", "description": "How two things are connected in the graph (a feature gate, person, team, SIG, KEP, folder or docs page): the shortest path between them, hop by hop, over ownership, membership, design decisions and docs structure. Use it for 'how is X related to Y'.",
         "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "max_hops": {"type": "integer"}}, "required": ["a", "b"]}},
        {"name": "sig_leaders", "description": "Chairs, tech leads and emeritus leads of a Kubernetes team (SIG), working group or committee, e.g. sig-storage.",
         "parameters": {"type": "object", "properties": {"sig": {"type": "string"}}, "required": ["sig"]}},
        {"name": "decision_lineage", "description": "The design decision (KEP) behind a feature gate or topic: status, owning team, authors, approvers, replaced or related KEPs.",
         "parameters": {"type": "object", "properties": {"kep_or_gate": {"type": "string"}}, "required": ["kep_or_gate"]}},
    ]

    # Which arguments of which tool name an entity, and what kind. Every one is resolved against the graph BEFORE the tool runs (lineage/agent/resolve.py).
    ENTITY_ARGS: dict[str, list[tuple[str, str]]] = {
        "drift_findings": [("sig", "sig")],
        "explain_finding": [("query", "finding")],
        "suggest_reviewers": [("query", "finding")],
        "gate_timeline": [("gate", "gate")],
        "doc_claims": [("page_or_section", "page")],
        "owners_of": [("path", "path"), ("as_of", "release")],
        "decision_lineage": [("kep_or_gate", "kep_or_gate")],
        "experts_for": [("path", "path")],
        "what_changed": [("entity", "gate_or_path"), ("from_release", "release"), ("to_release", "release")],
        "sig_leaders": [("sig", "sig")],
        "path_between": [("a", "entity"), ("b", "entity")],
    }

    def resolve_arguments(self, name: str, arguments: dict) -> tuple[dict, list[Resolution]]:
        """Resolve each entity argument; returns the arguments with canonical names (same name, normalised) and the failed resolutions."""
        out, failed = dict(arguments), []
        for arg, kind in self.ENTITY_ARGS.get(name, []):
            value = arguments.get(arg)
            if value in (None, "") and kind not in ("page", "finding"):
                continue
            repo = arguments.get("repo", "kubernetes")
            r = (resolve_path(self.g, str(value), repo) if kind == "path" else resolve_gate_or_path(self.g, str(value), repo) if kind == "gate_or_path"
                 else resolve_entity(self.g, str(value), repo) if kind == "entity"
                 else RESOLVERS[kind](self.g, str(value) if kind != "release" else value))
            if r.ok:
                if r.canonical is not None and kind in ("gate", "sig", "path"):
                    out[arg] = r.canonical
            else:
                failed.append((arg, r))
        return out, failed

    def call(self, name: str, arguments: dict) -> dict:
        t0 = time.perf_counter()
        if name not in {s["name"] for s in self.SCHEMAS}:
            return {"tool": name, "params": arguments, "evidence": [], "path": [], "status": "insufficient_evidence", "outcome": "unknown_entity", "error": "unknown tool",
                    "duration_ms": 0, "rows": 0}
        try:
            args, failed = self.resolve_arguments(name, arguments)
            if failed:  # no traversal runs for a name the graph does not know
                b = bundle(name, dict(arguments))
                b["status"], b["outcome"] = "insufficient_evidence", "unknown_entity"
                b["unresolved"] = [r.as_dict(arg) for arg, r in failed]
                b["params"]["note"] = "; ".join(
                    f"unknown {r.kind} {r.given!r}: {r.note}" + (f" (did you mean: {', '.join(r.suggestions)}?)" if r.suggestions else "") for _, r in failed
                ) + ". No lookup was run for it; the suggestions are not facts and must not be substituted."
            else:
                b = getattr(self, name)(**args)
                b["outcome"] = "answered_with_path" if b["status"] == "ok" else "not_in_graph"
        except TypeError as e:  # wrong arguments from the model
            return {"tool": name, "params": arguments, "evidence": [], "path": [], "status": "insufficient_evidence", "outcome": "not_in_graph", "error": f"bad arguments: {e}",
                    "duration_ms": round((time.perf_counter() - t0) * 1000), "rows": 0}
        b["validated_args"] = args if not failed else dict(arguments)  # the arguments the tool actually ran with (canonical names), or the given ones when none ran
        b["duration_ms"] = round((time.perf_counter() - t0) * 1000)
        b["rows"] = len(b["evidence"])
        return b
