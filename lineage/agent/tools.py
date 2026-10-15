"""Tier-1 tools: curated, parameterised, READ-ONLY Cypher behind a small interface.

Each tool returns an evidence bundle (CLAUDE.md §8): {tool, params, evidence[], path[], status}. Evidence items carry
the source_url that proves them; provenance comes from here, never from the LLM. status: ok | empty | insufficient_evidence.
"""
from __future__ import annotations

from falkordb import Graph

from lineage.graph.queries import LATEST, effective_owners, experts_for, gate_stage_change, official_vs_actual, orphaned_components, owners_diff
from lineage.graph.trace import Q_HISTORY, Q_KEPS, SEVERITY_ORDER, build_trace, rank_keps, stage_ranges

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


class ToolBox:
    def __init__(self, g: Graph):
        self.g = g

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
        if num != LATEST and (repo != "kubernetes" or num < 130):  # say WHY there is no answer, so the model can stop instead of searching on
            b["status"] = "insufficient_evidence"
            b["params"]["note"] = ("ownership history is available for kubernetes/kubernetes at releases 1.30-1.37 and latest; "
                                   "for the website (docs) repository only the latest commit is available, so there is no record of who owned it in an earlier release")
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
        if keps:
            b["path"] = [f"FeatureGate:{kep_or_gate}" if rows else f"query:{kep_or_gate}", f"KEP:{keps[0]['id']}"] + ([f"SIG:{keps[0]['sig']}"] if keps[0]["sig"] else [])
        return b

    def what_changed(self, entity: str, from_release: str, to_release: str, repo: str = "kubernetes") -> dict:
        """What changed between two releases: for a feature gate, its stage; for a code/docs path, who gained or lost an approver/reviewer role. Ownership history is sampled at releases 1.30-1.37 and now."""
        a, b = release_num(from_release), release_num(to_release)
        bndl = bundle("what_changed", {"entity": entity, "from_release": from_release, "to_release": to_release, "repo": repo})
        if self.g.ro_query("MATCH (g:FeatureGate {name: $g}) RETURN g.source_url", {"g": entity}).result_set:
            c = gate_stage_change(self.g, entity, a, b)
            put(bndl, "edge", "STAGE_CHANGE", f"FeatureGate:{entity}", None,
                {"from_release": from_release, "to_release": to_release, "stage_then": c["from"] or "none", "stage_now": c["to"] or "none",
                 "changed": c["from"] != c["to"]}, c["source_url"])
            bndl["path"] = [f"FeatureGate:{entity}"]
            return bndl
        if min(a, b) < 130:
            bndl["status"] = "insufficient_evidence"
            bndl["params"]["note"] = "ownership history is sampled only from release 1.30"
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
        return b

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
        {"name": "sig_leaders", "description": "Chairs, tech leads and emeritus leads of a Kubernetes team (SIG), working group or committee, e.g. sig-storage.",
         "parameters": {"type": "object", "properties": {"sig": {"type": "string"}}, "required": ["sig"]}},
        {"name": "decision_lineage", "description": "The design decision (KEP) behind a feature gate or topic: status, owning team, authors, approvers, replaced or related KEPs.",
         "parameters": {"type": "object", "properties": {"kep_or_gate": {"type": "string"}}, "required": ["kep_or_gate"]}},
    ]

    def call(self, name: str, arguments: dict) -> dict:
        if name not in {s["name"] for s in self.SCHEMAS}:
            return {"tool": name, "params": arguments, "evidence": [], "path": [], "status": "insufficient_evidence", "error": "unknown tool"}
        try:
            return getattr(self, name)(**arguments)
        except TypeError as e:  # wrong arguments from the model
            return {"tool": name, "params": arguments, "evidence": [], "path": [], "status": "insufficient_evidence", "error": f"bad arguments: {e}"}
