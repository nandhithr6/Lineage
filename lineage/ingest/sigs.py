"""Parse the Kubernetes org chart (community/sigs.yaml): SIGs (teams), working groups and committees.

Run: `uv run python -m lineage.ingest.sigs` -> writes snapshot/sigs.jsonl

Privacy: only the public GitHub handle and display name of each leader are kept. `email` and `company` are
dropped here, at ingestion, and never reach the snapshot or the graph (hackathon rules; CLAUDE.md §2).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from lineage.ingest.gates import repo_sha

log = logging.getLogger(__name__)

COMMUNITY_REPO = Path("data/raw/community")
SNAPSHOT = Path("snapshot/sigs.jsonl")
GROUPS = {"sigs": "sig", "workinggroups": "wg", "committees": "committee"}
ROLES = {"chairs": "chair", "tech_leads": "tech_lead", "emeritus_leads": "emeritus_lead"}


@dataclass(frozen=True)
class Group:
    id: str  # "sig-storage"
    name: str
    group_type: str  # sig | wg | committee
    label: str | None
    mission: str
    leaders: list[dict]  # [{"role", "handle", "name"}]
    subprojects: list[dict]  # [{"name", "owners_urls"}]
    source_url: str
    issues: list[str]


def parse_sigs(repo_root: Path = COMMUNITY_REPO) -> list[Group]:
    sha = repo_sha(repo_root)
    data = yaml.safe_load((repo_root / "sigs.yaml").read_text(encoding="utf-8"))
    url = f"https://github.com/kubernetes/community/blob/{sha}/sigs.yaml"
    out: list[Group] = []
    for key, gtype in GROUPS.items():
        for g in data.get(key) or []:
            issues: list[str] = []
            leaders = []
            for role_key, members in (g.get("leadership") or {}).items():
                if role_key not in ROLES:
                    issues.append(f"unknown leadership role {role_key!r} skipped")
                    continue
                for p in members or []:
                    if not p.get("github"):
                        issues.append(f"{role_key} entry without github handle skipped")
                        continue
                    leaders.append({"role": ROLES[role_key], "handle": str(p["github"]).lower(), "name": p.get("name")})
            subs = [{"name": s.get("name"), "owners_urls": list(s.get("owners") or [])} for s in g.get("subprojects") or []]
            out.append(Group(g["dir"], g.get("name", g["dir"]), gtype, g.get("label"),
                             (g.get("mission_statement") or "").strip()[:600], leaders, subs, url, issues))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    groups = parse_sigs()
    SNAPSHOT.write_text("".join(json.dumps(asdict(g), ensure_ascii=False) + "\n" for g in groups))
    leaders = {l["handle"] for g in groups for l in g.leaders}
    log.info("groups: %d (%s) | leader entries: %d | distinct people: %d | subprojects: %d | OWNERS urls: %d",
             len(groups), {t: sum(g.group_type == t for g in groups) for t in GROUPS.values()},
             sum(len(g.leaders) for g in groups), len(leaders), sum(len(g.subprojects) for g in groups),
             sum(len(s["owners_urls"]) for g in groups for s in g.subprojects))
    for g in groups:
        for i in g.issues:
            log.warning("%s: %s", g.id, i)


if __name__ == "__main__":
    main()
