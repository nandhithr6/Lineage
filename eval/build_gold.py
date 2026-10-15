"""Draft the Q&A gold set: 30 answerable questions (6 per category) + 6 unanswerable ones, from the RAW snapshot files.

Answers are computed from snapshot/*.jsonl (the parsed sources), not from the graph or the agent, so the truth does not depend on
the system being tested. Every question is DRAFT: Nan and Abiram must verify it against the cited source before it counts.

A check is {"all": [...]} (every string must appear, case-insensitive) or {"any": [...], "min": k} (at least k must appear); all checks must pass.
"phrase": true marks yes/no wording checks: they apply to written answers only, not to raw retrieved evidence.
Run: uv run python -m eval.build_gold
"""
from __future__ import annotations

import json
from pathlib import Path

from lineage.link.linker import read_jsonl

SNAP = Path("snapshot")
E = read_jsonl(SNAP / "owners_edges.jsonl")
M = read_jsonl(SNAP / "team_members.jsonl")
SIG = {s["id"]: s for s in read_jsonl(SNAP / "sigs.jsonl")}
KEP = {k["id"]: k for k in read_jsonl(SNAP / "keps.jsonl")}
GATE = {g["name"]: g for g in read_jsonl(SNAP / "gates.jsonl")}
LATEST = 138
YES = ["yes", "correct", "matches", "consistent", "up to date", "still accurate", "accurate"]
NO = ["stale", "out of date", "outdated", "not correct", "incorrect", "no,", "no.", "longer", "no longer", "wrong"]
DRAFT = "DRAFT: verify against the cited source before using in results"


NAMES = {l["handle"]: l["name"] for sg in SIG.values() for l in sg["leaders"] if l.get("name")}


def alts(handles: list[str]) -> list:
    """Each handle becomes [handle, display name] when the org chart gives a name: an answer may say either (people write 'Xing Yang')."""
    return [x if isinstance(x, list) else ([x, NAMES[x]] if x in NAMES else x) for x in handles]


def at(e: dict, n: int) -> bool:
    return e["valid_from"] <= n and (e["valid_to"] is None or e["valid_to"] > n)


def people(repo: str, d: str, role: str, n: int = LATEST) -> list[str]:
    return sorted(e["subject"] for e in E if e["repo"] == repo and e["dir"] == d and e["role"] == role and e["subject_kind"] == "person" and at(e, n))


def team_members(team: str, n: int = LATEST) -> list[str]:
    return sorted(m["handle"] for m in M if m["team"] == team and at(m, n))


def src(repo: str, d: str, role: str | None = None, n: int = LATEST) -> list[str]:
    return sorted({e["source_url"] for e in E if e["repo"] == repo and e["dir"] == d and at(e, n) and (role is None or e["role"] == role)})[:2]


def leaders(sig: str, role: str) -> list[str]:
    return sorted(x["handle"] for x in SIG[sig]["leaders"] if x["role"] == role)


def kep_for(gate: str) -> dict:
    cands = [k for k in KEP.values() if gate in k["feature_gates"]]
    assert cands, f"no kep.yaml lists {gate}"
    return cands[0]


def q(qid, cat, question, answer, checks, sources, tool, args, notes=""):
    assert all(s for s in sources), f"{qid}: missing source"
    checks = [{**c, **{k: alts(c[k]) for k in ("all", "any") if k in c and not c.get("phrase")}} for c in checks]
    return {"id": qid, "category": cat, "question": question, "answer": answer, "checks": checks, "sources": sources,
            "oracle_tool": {"name": tool, "args": args}, "notes": notes, "status": DRAFT}


def active_reviewers(prefix: str, months: int = 12, top: int = 10) -> tuple[list[str], list[str]]:
    """Top reviewers under a folder, computed straight from snapshot/prs.jsonl (not from the graph): distinct merged PRs on master reviewed in the last
    `months` before the snapshot date, bots and self-reviews excluded, backports and repo-wide PRs excluded (same documented rules as the product)."""
    import datetime as dt
    from lineage.graph.loader import CROSS_CUTTING, is_bot, is_cherry_pick, nearest_codepath
    known = {c["dir"] for c in read_jsonl(SNAP / "codepaths.jsonl") if c["repo"] == "kubernetes"}
    cutoff = (dt.date(2026, 10, 15) - dt.timedelta(days=int(months * 30.44))).isoformat()
    count: dict[str, set[int]] = {}
    urls: dict[str, str] = {}
    for p in read_jsonl(SNAP / "prs.jsonl"):
        paths = {nearest_codepath(d, known) for d in p["dirs"]} - {None}
        if is_cherry_pick(p["title"], p.get("base_ref")) or len(paths) >= CROSS_CUTTING or p["files_truncated"] or not any(x == prefix or x.startswith(prefix + "/") for x in paths):
            continue
        for r in p["reviews"]:
            if r["login"] != p["author"] and not is_bot(r["login"]) and (r["at"] or "") >= cutoff:
                count.setdefault(r["login"], set()).add(p["number"])
                urls.setdefault(r["login"], p["url"])
    ranked = sorted(count, key=lambda h: (-len(count[h]), h))[:top]
    return ranked, [urls[h] for h in ranked[:3]]


def build() -> list[dict]:
    out: list[dict] = []
    job_now, job_130, job_132 = (people("kubernetes", "pkg/controller/job", "APPROVER_OF", n) for n in (138, 130, 132))
    assert job_130 == ["alculquicondor"] and job_132 == ["mimowo"]
    docs_approvers = team_members("website:sig-docs-en-owners")
    storage_approvers = team_members("kubernetes:sig-storage-approvers")
    k361 = KEP["sig-storage/361-local-ephemeral-storage-isolation"]
    vol_active, vol_urls = active_reviewers("pkg/volume")
    assert len(vol_active) >= 5 and vol_urls
    assert len(docs_approvers) >= 5 and len(storage_approvers) >= 4

    # ownership
    out += [
        q("own1", "ownership", "Who are the individual approvers, not teams, listed in the OWNERS file of pkg/controller/job in kubernetes/kubernetes today?",
          f"{', '.join(job_now)} (the team alias sig-apps-approvers is also listed)", [{"all": job_now}], src("kubernetes", "pkg/controller/job", "APPROVER_OF"), "owners_of", {"path": "pkg/controller/job"}),
        q("own2", "ownership", "Which team alias approves changes under pkg/volume in kubernetes/kubernetes?", "sig-storage-approvers",
          [{"all": ["sig-storage-approvers"]}], src("kubernetes", "pkg/volume", "APPROVER_OF"), "owners_of", {"path": "pkg/volume"}),
        q("own3", "ownership", "Name two people who can approve a change to the Resource Quotas page of the Kubernetes docs.",
          f"Any of the docs approvers, e.g. {', '.join(docs_approvers[:4])}", [{"any": docs_approvers, "min": 2}],
          src("website", "content/en", "APPROVER_OF"), "owners_of", {"path": "content/en/docs/concepts/policy/resource-quotas.md", "repo": "website"}),
        q("own4", "ownership", "Who are the chairs of sig-storage?", ", ".join(leaders("sig-storage", "chair")), [{"all": leaders("sig-storage", "chair")}],
          [SIG["sig-storage"]["source_url"]], "sig_leaders", {"sig": "sig-storage"}),
        q("own5", "ownership", "Who are the tech leads of sig-node?", ", ".join(leaders("sig-node", "tech_lead")), [{"all": leaders("sig-node", "tech_lead")}],
          [SIG["sig-node"]["source_url"]], "sig_leaders", {"sig": "sig-node"}),
        q("own6", "ownership", "Who is listed as an emeritus approver in the root OWNERS file of the Kubernetes website repository?",
          ", ".join(people("website", "", "EMERITUS_APPROVER_OF")), [{"all": people("website", "", "EMERITUS_APPROVER_OF")}],
          src("website", "", "EMERITUS_APPROVER_OF"), "owners_of", {"path": "", "repo": "website"}),
    ]
    # expertise (declared expertise only: PR/review data is not loaded yet)
    out += [
        q("exp1", "expertise", "Who authored the design document 'Local Ephemeral Storage Capacity Isolation'?", ", ".join(k361["authors"]), [{"all": k361["authors"]}],
          [k361["source_url"]], "decision_lineage", {"kep_or_gate": "local-ephemeral-storage-isolation"}),
        q("exp2", "expertise", "Who approved the design document 'Local Ephemeral Storage Capacity Isolation'?", ", ".join(k361["approvers"]), [{"all": k361["approvers"]}],
          [k361["source_url"]], "decision_lineage", {"kep_or_gate": "local-ephemeral-storage-isolation"}),
        q("exp3", "expertise", "Who actively reviews changes to pkg/volume? Name two people.", f"two of: {', '.join(vol_active[:6])} (most distinct PRs reviewed in the last 12 months)",
          [{"any": vol_active, "min": 2}], vol_urls, "experts_for", {"path": "pkg/volume"},
          "ACTUAL review activity from snapshot/prs.jsonl (not the OWNERS list). Earlier draft asked 'who should I ask about pkg/volume' with declared owners as the only accepted answer; the agent's activity-based answer was reasonable"),
        q("exp4", "expertise", "Who can approve changes in staging/src/k8s.io/mount-utils?", ", ".join(people("kubernetes", "staging/src/k8s.io/mount-utils", "APPROVER_OF")),
          [{"all": people("kubernetes", "staging/src/k8s.io/mount-utils", "APPROVER_OF")}], src("kubernetes", "staging/src/k8s.io/mount-utils", "APPROVER_OF"), "owners_of", {"path": "staging/src/k8s.io/mount-utils"}),
        q("exp5", "expertise", "Who are the tech leads of sig-storage, whom I could ask about storage design?", ", ".join(leaders("sig-storage", "tech_lead")),
          [{"all": leaders("sig-storage", "tech_lead")}], [SIG["sig-storage"]["source_url"]], "sig_leaders", {"sig": "sig-storage"}),
        q("exp6", "expertise", "Who chairs sig-node?", ", ".join(leaders("sig-node", "chair")), [{"all": leaders("sig-node", "chair")}], [SIG["sig-node"]["source_url"]], "sig_leaders", {"sig": "sig-node"}),
    ]
    # time travel
    lsci = GATE["LocalStorageCapacityIsolation"]
    phip = GATE["PodHostIPs"]
    out += [
        q("tt1", "time_travel", "Who was the individual approver of pkg/controller/job in release 1.30?", ", ".join(job_130), [{"all": job_130}],
          src("kubernetes", "pkg/controller/job", "APPROVER_OF", 130), "owners_of", {"path": "pkg/controller/job", "as_of": "1.30"}),
        q("tt2", "time_travel", "Who was the individual approver of pkg/controller/job in release 1.32?", ", ".join(job_132), [{"all": job_132}],
          src("kubernetes", "pkg/controller/job", "APPROVER_OF", 132), "owners_of", {"path": "pkg/controller/job", "as_of": "1.32"}),
        q("tt3", "time_travel", "What maturity stage was the LocalStorageCapacityIsolation feature gate in during release 1.9?", "alpha", [{"all": ["alpha"]}],
          [lsci["source_url"]], "gate_timeline", {"gate": "LocalStorageCapacityIsolation"}),
        q("tt4", "time_travel", "What maturity stage was the LocalStorageCapacityIsolation feature gate in during release 1.20?", "beta", [{"all": ["beta"]}],
          [lsci["source_url"]], "gate_timeline", {"gate": "LocalStorageCapacityIsolation"}),
        q("tt5", "time_travel", "Who stopped being a reviewer of pkg/apis/core/v1 between releases 1.30 and 1.32?", "ncdc",
          [{"all": ["ncdc"]}], sorted({e["source_url"] for e in E if e["dir"] == "pkg/apis/core/v1" and e["subject"] == "ncdc"})[:1], "owners_of", {"path": "pkg/apis/core/v1", "as_of": "1.30"},
          "ncdc is a reviewer at 1.30 and absent at 1.32 in the sampled OWNERS files"),
        q("tt6", "time_travel", "What maturity stage was the PodHostIPs feature gate in during release 1.30?", "beta and stable (the source lists both for 1.30)",
          [{"all": ["beta", "stable"]}], [phip["source_url"]], "gate_timeline", {"gate": "PodHostIPs"}, "a release where the source lists two stages (overlap)"),
    ]
    # decision lineage
    avds, ctz, vsds = kep_for("AnyVolumeDataSource"), kep_for("CronJobTimeZone"), kep_for("VolumeSnapshotDataSource")
    replacer = next(k for k in KEP.values() if k["id"] == "sig-auth/3299-kms-v2-improvements")
    assert replacer["replaces"] == ["sig-auth/3130-kms-observability"]
    out += [
        q("dl1", "decision_lineage", "Which design document (KEP) introduced the AnyVolumeDataSource feature gate?", avds["id"], [{"all": [["1495-volume-populators", "KEP-1495", "KEP 1495", "1495"]]}],
          [avds["source_url"]], "decision_lineage", {"kep_or_gate": "AnyVolumeDataSource"}),
        q("dl2", "decision_lineage", "Which team owns the design document behind the VolumeSnapshotDataSource feature gate?", vsds["owning_sig"], [{"all": [vsds["owning_sig"]]}],
          [vsds["source_url"]], "decision_lineage", {"kep_or_gate": "VolumeSnapshotDataSource"}),
        q("dl3", "decision_lineage", "Who approved the design document behind the CronJobTimeZone feature gate?", ", ".join(ctz["approvers"]), [{"all": ctz["approvers"]}],
          [ctz["source_url"]], "decision_lineage", {"kep_or_gate": "CronJobTimeZone"}),
        q("dl4", "decision_lineage", "In which Kubernetes release did the design document behind CronJobTimeZone reach stable?", ctz["milestone"]["stable"], [{"all": [ctz["milestone"]["stable"].lstrip("v")]}],
          [ctz["source_url"]], "decision_lineage", {"kep_or_gate": "CronJobTimeZone"}),
        q("dl5", "decision_lineage", "Which earlier design document does the KEP sig-auth/3299-kms-v2-improvements replace?", replacer["replaces"][0], [{"all": ["3130-kms-observability"]}],
          [replacer["source_url"]], "decision_lineage", {"kep_or_gate": "3299-kms-v2-improvements"}),
        q("dl6", "decision_lineage", "What is the status and maturity stage of the design document 'Local Ephemeral Storage Capacity Isolation'?", f"status {k361['status']}, stage {k361['stage']}",
          [{"all": [k361["status"], k361["stage"]]}], [k361["source_url"]], "decision_lineage", {"kep_or_gate": "local-ephemeral-storage-isolation"}),
    ]
    # drift
    out += [
        q("dr1", "drift", "Is the 'alpha since v1.8' label in the 'Quota for local ephemeral storage' section of the Resource Quotas docs page still correct?",
          "No: the gate LocalStorageCapacityIsolation became stable in 1.25 (since removed)", [{"all": ["stable", "1.25"]}, {"any": NO, "min": 1, "phrase": True}],
          [lsci["source_url"], "https://github.com/kubernetes/website/blob/e91eff3b59c0bf813193202a4f193d37beb7efd7/content/en/docs/concepts/policy/resource-quotas.md#L194"], "explain_finding", {"query": "resource-quotas"},
          "link verified by hand"),
        q("dr2", "drift", "Is the 'stable since v1.27' label in the time zones section of the CronJob docs page correct?", "Yes: CronJobTimeZone became stable in 1.27",
          [{"all": ["1.27"]}, {"any": YES, "min": 1, "phrase": True}], [GATE["CronJobTimeZone"]["source_url"]], "doc_claims", {"page_or_section": "cron-jobs"}, "negative control"),
        q("dr3", "drift", "Is the 'beta since v1.24' label on the Volume Populators and Data Sources docs page still correct?", "No: AnyVolumeDataSource is stable since 1.33",
          [{"all": ["stable", "1.33"]}, {"any": NO, "min": 1, "phrase": True}], [GATE["AnyVolumeDataSource"]["source_url"]], "explain_finding", {"query": "volume-populators"}),
        q("dr4", "drift", "Which Kubernetes docs pages have confirmed out-of-date maturity labels?", "Resource Quotas; Volume Populators and Data Sources",
          [{"all": ["resource quotas", "volume populators"]}], [lsci["source_url"], GATE["AnyVolumeDataSource"]["source_url"]], "drift_findings", {"severity": "finding"},
          "answer = the 2 confirmed findings at snapshot time; review items are not findings"),
        q("dr5", "drift", "Is the 'stable since v1.22' label in the automatic labelling section of the Namespaces docs page correct?", "Yes: NamespaceDefaultLabelName became stable in 1.22",
          [{"all": ["1.22"]}, {"any": YES, "min": 1, "phrase": True}], [GATE["NamespaceDefaultLabelName"]["source_url"]], "doc_claims", {"page_or_section": "namespaces"}, "negative control"),
        q("dr6", "drift", "Is the 'stable since v1.25' label in the Pod OS section of the Pods docs page correct?", "Yes: IdentifyPodOS became stable in 1.25",
          [{"all": ["1.25"]}, {"any": YES, "min": 1, "phrase": True}], [GATE["IdentifyPodOS"]["source_url"]], "doc_claims", {"page_or_section": "Pod OS"}, "negative control; the linker could not link this label confidently"),
    ]
    # multi-hop
    out += [
        q("mh1", "multi_hop", "Who could merge, and who could technically verify, a fix for the out-of-date label on the Resource Quotas page?",
          f"Docs approvers (e.g. {', '.join(docs_approvers[:3])}) and storage experts (msau42, xing-yang, gnufied, jsafrane)",
          [{"any": docs_approvers, "min": 1}, {"any": ["msau42", "xing-yang", "gnufied", "jsafrane"], "min": 1}], src("website", "content/en", "APPROVER_OF") + src("kubernetes", "pkg/volume", "APPROVER_OF"),
          "explain_finding", {"query": "resource-quotas"}),
        q("mh2", "multi_hop", "Which team owns the design document behind the feature that the Resource Quotas page labels alpha since v1.8?", "sig-storage", [{"all": ["sig-storage"]}],
          [k361["source_url"]], "explain_finding", {"query": "resource-quotas"}),
        q("mh3", "multi_hop", "Who approved the design document behind the feature that the Volume Populators and Data Sources page labels beta?", ", ".join(avds["approvers"]), [{"all": avds["approvers"]}],
          [avds["source_url"]], "explain_finding", {"query": "volume-populators"}),
        q("mh4", "multi_hop", "What is the current maturity stage of the feature gate behind the 'Volume populators and data sources' label, and since which release?", "stable since 1.33",
          [{"all": ["stable", "1.33"]}], [GATE["AnyVolumeDataSource"]["source_url"]], "explain_finding", {"query": "volume-populators"}),
        q("mh5", "multi_hop", "Who chairs the team that owns the design document 'Local Ephemeral Storage Capacity Isolation'?", ", ".join(leaders("sig-storage", "chair")),
          [{"all": leaders("sig-storage", "chair")}], [k361["source_url"], SIG["sig-storage"]["source_url"]], "decision_lineage", {"kep_or_gate": "local-ephemeral-storage-isolation"},
          "needs two hops: KEP -> owning team -> chairs; the oracle tool only covers the first"),
        q("mh6", "multi_hop", "Which people approved the design document for the gate that the Resource Quotas page labels alpha, and are any of them also leaders of the owning team?",
          "msau42 and xing-yang approved KEP 361; both lead sig-storage (tech lead and chair)", [{"all": k361["approvers"]}], [k361["source_url"], SIG["sig-storage"]["source_url"]],
          "explain_finding", {"query": "resource-quotas"}),
    ]
    # unanswerable: the right behaviour is a refusal
    un = [
        ("un1", "Who reviewed the most pull requests in the kubernetes/website repository last year?", "pull-request data is loaded for kubernetes/kubernetes only (sig-storage, sig-node, sig-apps labels). Earlier drafts asked about pkg/volume and then pkg/scheduler; both turned out to be answerable from the PR data, so the premise was wrong"),
        ("un2", "Who owned the Kubernetes website docs in release 1.30?", "docs ownership is sampled at HEAD only"),
        ("un3", "Which feature gates will be removed in release 1.40?", "the future is not in the data"),
        ("un4", "What is the email address of the chair of sig-storage?", "emails are dropped at ingestion (personal data)"),
        ("un5", "Which docs page is most popular with readers?", "no traffic data"),
        ("un6", "Who approved the design document behind the NoSuchGateXyz feature gate?", "the gate does not exist"),
    ]
    out += [{"id": i, "category": "unanswerable", "question": qq, "answer": "refusal expected (insufficient evidence)", "checks": [{"refusal": True}], "sources": [],
             "oracle_tool": None, "notes": why, "status": DRAFT} for i, qq, why in un]
    return out


def main() -> None:
    qs = build()
    Path("eval/gold_questions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in qs))
    from collections import Counter
    print(len(qs), "questions:", dict(Counter(x["category"] for x in qs)))
    review = ["# Gold questions: DRAFT for human verification", "",
              "Answers were computed from the raw snapshot files. **Check each one against its source link before it is used in any result.** Tick the box when verified.", ""]
    for x in qs:
        review += [f"- [ ] **{x['id']}** ({x['category']}): {x['question']}", f"  - expected: {x['answer']}"] + [f"  - source: {s}" for s in x["sources"]] + ([f"  - note: {x['notes']}"] if x["notes"] else [])
    Path("eval/gold_review.md").write_text("\n".join(review) + "\n")


if __name__ == "__main__":
    main()
