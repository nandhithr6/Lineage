"""Parse KEPs (Kubernetes Enhancement Proposals) from the enhancements repo.

Run: `uv run python -m lineage.ingest.keps` -> writes snapshot/keps.jsonl and logs every data oddity.

A KEP is a design-decision document: keps/<sig>/<number>-<slug>/kep.yaml (metadata) + README.md (prose).
KEP node key = "<sig>/<number>-<slug>" because kep-number is not unique (templates use 0, 2133 appears twice).
KEP -> gate links come from two places, kept separate with different confidence:
  kep_yaml       - the gate is listed in kep.yaml `feature-gates` (confidence 1.0)
  readme_mention - the gate's exact name appears in the README (0.7 next to "feature gate", otherwise 0.4)
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from lineage.ingest.gates import SNAPSHOT as GATES_SNAPSHOT
from lineage.ingest.gates import repo_sha

log = logging.getLogger(__name__)

ENHANCEMENTS_REPO = Path("data/raw/enhancements")
SNAPSHOT = Path("snapshot/keps.jsonl")

STATUS_TYPOS = {"imlpemented": "implemented"}  # only unambiguous typos are mapped
VALID_STATUS = {"provisional", "implementable", "implemented", "deferred", "rejected", "withdrawn", "replaced"}
PLACEHOLDERS = {"", "n/a", "na", "none", "tbd", "todo", "-"}
_GATE_WORDS = re.compile(r"feature[\s-]*gates?", re.I)
_SUMMARY = re.compile(r"^##\s+Summary\s*$(.*?)(?=^##\s|\Z)", re.S | re.M | re.I)


class _Loader(yaml.SafeLoader):
    """SafeLoader that leaves dates as strings: two real kep.yaml files contain invalid dates (month 13)."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


@dataclass(frozen=True)
class Kep:
    id: str  # "sig-storage/361-local-ephemeral-storage-isolation"
    number: int | None
    title: str
    owning_sig: str
    participating_sigs: list[str]
    status_raw: str | None
    status: str | None  # normalised, None if unmapped
    stage: str | None
    latest_milestone: str | None
    milestone: dict
    creation_date: str | None
    authors: list[str]  # lowercased GitHub handles, no "@"
    approvers: list[str]
    reviewers: list[str]
    feature_gates: list[str]  # from kep.yaml
    readme_gates: list[dict]  # [{"gate", "confidence"}] found in README text, excluding kep.yaml gates
    replaces: list[str]  # resolved KEP ids
    see_also: list[str]
    superseded_by: list[str]
    unresolved_refs: list[str]  # URLs and other references we keep as text
    summary: str  # README "Summary" section, for similarity later
    source_url: str
    issues: list[str] = field(default_factory=list)


def handles(values: object, issues: list[str], label: str) -> list[str]:
    out = []
    for v in values or []:
        s = str(v).strip()
        if s.startswith("@") and len(s) > 1:
            out.append(s[1:].lower())
        elif s.lower() not in PLACEHOLDERS:
            issues.append(f"{label} entry without @handle skipped: {s!r}")
    return out


def normalise_status(raw: object, issues: list[str]) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in STATUS_TYPOS:
        issues.append(f"status typo {raw!r} mapped to {STATUS_TYPOS[s]!r}")
        return STATUS_TYPOS[s]
    if s in VALID_STATUS:
        return s
    issues.append(f"unmapped status {raw!r}" + (" (template placeholder)" if "|" in s else ""))
    return None


def resolve_refs(values: object, known: set[str], renamed: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Split references into KEP ids we can resolve ('/keps/sig-node/123-slug[.md]') and everything else.

    A KEP folder is sometimes renamed after other KEPs linked to it ('4438-sidecar-restart-termination' became '4438-container-restart-termination'). When the exact path is gone,
    the reference still resolves if the same team has exactly ONE KEP with the same number; the rename is recorded in `renamed` so the gap is visible, not hidden.
    """
    resolved, other = [], []
    for v in values or []:
        s = str(v).strip()
        if s.lower() in PLACEHOLDERS:
            continue
        m = re.match(r"^/?keps/([\w-]+)/([\w.-]+?)(?:\.md)?(?:/.*)?$", s)
        if m and f"{m.group(1)}/{m.group(2)}" in known:
            resolved.append(f"{m.group(1)}/{m.group(2)}")
            continue
        if m and (num := re.match(r"(\d+)-", m.group(2))):
            same_number = [k for k in known if k.startswith(f"{m.group(1)}/{num.group(1)}-")]
            if len(same_number) == 1:
                resolved.append(same_number[0])
                if renamed is not None:
                    renamed.append(f"{m.group(1)}/{m.group(2)} -> {same_number[0]}")
                continue
        other.append(s)
    return resolved, other


def gate_regex(names: list[str]) -> re.Pattern[str]:
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])({alt})(?![A-Za-z0-9])")


def readme_gate_mentions(text: str, rx: re.Pattern[str], exclude: set[str]) -> list[dict]:
    best: dict[str, float] = {}
    for m in rx.finditer(text):
        window = text[max(0, m.start() - 60) : m.end() + 60]
        conf = 0.7 if _GATE_WORDS.search(window) else 0.4
        best[m.group(1)] = max(best.get(m.group(1), 0.0), conf)
    return [{"gate": g, "confidence": c} for g, c in sorted(best.items()) if g not in exclude]


def parse_keps(repo_root: Path = ENHANCEMENTS_REPO, gate_names: list[str] | None = None) -> list[Kep]:
    sha = repo_sha(repo_root)
    rx = gate_regex(gate_names) if gate_names else None
    files = sorted((repo_root / "keps").glob("*/*/kep.yaml"))
    ids = {f"{f.parent.parent.name}/{f.parent.name}" for f in files}
    out: list[Kep] = []
    for f in files:
        kep_id = f"{f.parent.parent.name}/{f.parent.name}"
        issues: list[str] = []
        if "kep-template" in kep_id:
            continue  # NNNN-kep-template is the template, not a KEP
        y = yaml.load(f.read_text(encoding="utf-8"), Loader=_Loader) or {}
        gates = []
        for g in y.get("feature-gates") or []:
            name = g.get("name") if isinstance(g, dict) else g
            if name and str(name).strip():
                gates.append(str(name).strip())
            else:
                issues.append(f"feature-gates entry without a name skipped: {g!r}")
        readme = f.parent / "README.md"
        text = readme.read_text(encoding="utf-8", errors="replace") if readme.exists() else ""
        if not readme.exists():
            issues.append("no README.md")
        renamed: list[str] = []
        replaces, r_other = resolve_refs(y.get("replaces"), ids, renamed)
        see_also, s_other = resolve_refs(y.get("see-also"), ids, renamed)
        superseded, d_other = resolve_refs(y.get("superseded-by"), ids, renamed)
        issues.extend(f"reference followed a renamed KEP folder: {r}" for r in renamed)
        number = y.get("kep-number")
        try:
            number = int(number)
        except (TypeError, ValueError):
            issues.append(f"kep-number not an integer: {number!r}")
            number = None
        summary = _SUMMARY.search(text)
        out.append(Kep(
            id=kep_id, number=number, title=str(y.get("title", "")), owning_sig=str(y.get("owning-sig", "")),
            participating_sigs=[str(s) for s in y.get("participating-sigs") or []],
            status_raw=None if y.get("status") is None else str(y.get("status")),
            status=normalise_status(y.get("status"), issues), stage=y.get("stage"),
            latest_milestone=None if y.get("latest-milestone") is None else str(y.get("latest-milestone")),
            milestone={k: str(v) for k, v in (y.get("milestone") or {}).items()},
            creation_date=None if y.get("creation-date") is None else str(y.get("creation-date")),
            authors=handles(y.get("authors"), issues, "author"), approvers=handles(y.get("approvers"), issues, "approver"),
            reviewers=handles(y.get("reviewers"), issues, "reviewer"), feature_gates=gates,
            readme_gates=readme_gate_mentions(text, rx, set(gates)) if rx else [],
            replaces=replaces, see_also=see_also, superseded_by=superseded, unresolved_refs=r_other + s_other + d_other,
            summary=(summary.group(1).strip()[:1500] if summary else ""),
            source_url=f"https://github.com/kubernetes/enhancements/blob/{sha}/keps/{kep_id}/kep.yaml", issues=issues,
        ))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    gate_names = [json.loads(line)["name"] for line in GATES_SNAPSHOT.read_text().splitlines() if line.strip()]
    keps = parse_keps(gate_names=gate_names)
    SNAPSHOT.write_text("".join(json.dumps(asdict(k), ensure_ascii=False) + "\n" for k in keps))
    log.info("KEPs: %d -> %s", len(keps), SNAPSHOT)
    log.info("status: %s", dict(Counter(k.status for k in keps)))
    log.info("with kep.yaml gates: %d | with README-only gate mentions: %d", sum(bool(k.feature_gates) for k in keps),
             sum(bool(k.readme_gates) and not k.feature_gates for k in keps))
    log.info("resolved refs: replaces %d, see-also %d | unresolved kept as text: %d",
             sum(len(k.replaces) for k in keps), sum(len(k.see_also) for k in keps), sum(len(k.unresolved_refs) for k in keps))
    kinds = Counter(re.sub(r"[\"'@].*", "", i)[:40] for k in keps for i in k.issues)
    log.info("data issues by kind: %s", dict(kinds.most_common(8)))
    for k in keps:
        for i in k.issues:
            if i.startswith(("unmapped status", "status typo", "kep-number", "feature-gates entry")):
                log.warning("%s: %s", k.id, i)


if __name__ == "__main__":
    main()
