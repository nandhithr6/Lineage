"""Parse Kubernetes feature-gate pages (website repo) into normalized records.

Run: `uv run python -m lineage.ingest.gates` -> writes snapshot/gates.jsonl and logs a summary.

Two layers, kept separate on purpose:
  * `parse_gate_file` is faithful to the file: the stage *ranges* exactly as written.
  * `expand_to_releases` derives one row per release (these become STAGE_IN edges).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import frontmatter

log = logging.getLogger(__name__)

WEBSITE_REPO = Path("data/raw/website")
GATES_DIR = Path("content/en/docs/reference/command-line-tools-reference/feature-gates")
SNAPSHOT = Path("snapshot/gates.jsonl")
CURRENT_RELEASE = "1.37"  # docs site version, see data/releases/schedule.yaml

_DOC_LINK = re.compile(r"\]\((/docs/[^)\s#]+)")


@dataclass(frozen=True)
class StageRange:
    stage: str  # alpha | beta | stable | deprecated
    from_version: str  # "1.25" (kept as written)
    to_version: str | None  # None = open-ended in the source file
    default_value: bool | None
    locked: bool


@dataclass(frozen=True)
class Gate:
    name: str
    removed: bool
    stages: list[StageRange]
    description: str
    doc_links: list[str]  # internal /docs/... link targets, used later to link doc sections to gates
    source_path: str  # repo-relative
    source_url: str  # GitHub permalink pinned to a commit
    former_titles: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # data oddities we noticed, never silently fixed


def version_key(v: str) -> tuple[int, int]:
    """'1.25' -> (1, 25). Sortable, unlike the string."""
    major, minor = v.split(".")[:2]
    return int(major), int(minor)


def release_num(v: str) -> int:
    """'1.25' -> 125, the sortable number stored on Release nodes."""
    major, minor = version_key(v)
    return major * 100 + minor


def permalink(sha: str, rel_path: str) -> str:
    return f"https://github.com/kubernetes/website/blob/{sha}/{rel_path}"


def parse_gate_file(path: Path, repo_root: Path, sha: str) -> Gate | None:
    """Return a Gate, or None if the file is not a feature-gate page (e.g. index.md)."""
    post = frontmatter.load(path)
    meta = post.metadata
    if meta.get("content_type") != "feature_gate":
        return None

    issues: list[str] = []
    stages = [
        StageRange(
            stage=str(s["stage"]),
            from_version=str(s["fromVersion"]),
            to_version=str(s["toVersion"]) if s.get("toVersion") is not None else None,
            default_value=s.get("defaultValue"),
            locked=bool(s.get("locked", False)),
        )
        for s in meta.get("stages") or []
    ]
    removed = bool(meta.get("removed", False))
    name = str(meta["title"])
    if name != path.stem:
        issues.append(f"title {name!r} differs from filename {path.stem!r}")
    if not stages:
        issues.append("no stages")
    elif removed and stages[-1].to_version is None:
        issues.append("removed but last stage has no toVersion: removal release unknown")
    for cur, nxt in zip(stages, stages[1:]):
        if cur.to_version is None:
            issues.append(
                f"stage {cur.stage} from {cur.from_version} has no toVersion but is followed by "
                f"{nxt.stage} from {nxt.from_version}: end inferred as the release before"
            )

    rel_path = path.relative_to(repo_root).as_posix()
    return Gate(
        name=name,
        removed=removed,
        stages=stages,
        description=post.content.strip(),
        doc_links=sorted(set(_DOC_LINK.findall(post.content))),
        source_path=rel_path,
        source_url=permalink(sha, rel_path),
        former_titles=[str(t) for t in meta.get("former_titles") or []],
        issues=issues,
    )


def expand_to_releases(gate: Gate, current: str = CURRENT_RELEASE) -> list[dict]:
    """One row per (release, stage). Ranges are inclusive on both ends.

    A stage with no end that is followed by another stage ends the release before that one starts
    (inferred; the parser records an issue). The last open-ended stage on a live gate runs up to
    `current`. On a *removed* gate its end is unknown, so we emit only its from-version rather than
    guess (the gate carries an issue noting this).
    """
    rows: list[dict] = []
    for i, s in enumerate(gate.stages):
        start = version_key(s.from_version)
        following = gate.stages[i + 1] if i + 1 < len(gate.stages) else None
        if s.to_version is not None:
            end = version_key(s.to_version)
        elif following is not None:
            nxt = version_key(following.from_version)
            end = (nxt[0], nxt[1] - 1)
        elif gate.removed:
            end = start
        else:
            end = version_key(current)
        for minor in range(start[1], end[1] + 1):  # all k8s releases so far are 1.x
            rows.append(
                {
                    "gate": gate.name,
                    "release": f"{start[0]}.{minor}",
                    "release_num": start[0] * 100 + minor,
                    "stage": s.stage,
                    "default_value": s.default_value,
                    "locked": s.locked,
                    "overlap": False,
                    "inferred_end": s.to_version is None and following is not None,
                    "source_url": gate.source_url,
                }
            )
    return _merge_overlaps(rows)


def _merge_overlaps(rows: list[dict]) -> list[dict]:
    """Drop exact (release, stage) duplicates; flag releases where the source lists two different stages.

    Kubernetes sometimes lists a stage change inside one release (beta to 1.30, stable from 1.30), or
    splits one stage at a patch version (1.35.3 / 1.35.4). We keep every distinct stage and mark it
    `overlap` instead of choosing a winner.
    """
    seen: dict[tuple[str, str], dict] = {}
    for r in rows:
        seen.setdefault((r["release"], r["stage"]), r)
    stages_per_release: dict[str, int] = {}
    for release, _ in seen:
        stages_per_release[release] = stages_per_release.get(release, 0) + 1
    out = list(seen.values())
    for r in out:
        r["overlap"] = stages_per_release[r["release"]] > 1
    return out


def repo_sha(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def parse_all(repo_root: Path = WEBSITE_REPO) -> list[Gate]:
    sha = repo_sha(repo_root)
    gates = []
    for p in sorted((repo_root / GATES_DIR).glob("*.md")):
        if p.stem == "_index":
            continue
        gate = parse_gate_file(p, repo_root, sha)
        if gate is not None:
            gates.append(gate)
    return gates


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    gates = parse_all()
    SNAPSHOT.parent.mkdir(exist_ok=True)
    with SNAPSHOT.open("w") as f:
        for g in gates:
            f.write(json.dumps(asdict(g), ensure_ascii=False) + "\n")
    rows = sum(len(expand_to_releases(g)) for g in gates)
    log.info("gates: %d (removed: %d) -> %s", len(gates), sum(g.removed for g in gates), SNAPSHOT)
    log.info("derived per-release rows: %d", rows)
    for g in gates:
        for issue in g.issues:
            log.warning("%s: %s", g.name, issue)


if __name__ == "__main__":
    main()
