"""Extract `feature-state` labels (the "Beta since v1.10" badges) from the Kubernetes docs.

Run: `uv run python -m lineage.ingest.docs_labels` -> writes snapshot/doc_labels.jsonl and logs counts.

A label record keeps three things: the shortcode itself, the section around it (nearest heading above),
and the internal links in that section. The section text and links are what the linker later uses to
guess which feature gate a hard-coded label is about.

Not extracted: shortcodes inside fenced code blocks, and Hugo-escaped examples (`{{</* ... */>}}`).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import frontmatter

from lineage.ingest.gates import WEBSITE_REPO, repo_sha

log = logging.getLogger(__name__)

DOCS_DIR = Path("content/en/docs")
SNAPSHOT = Path("snapshot/doc_labels.jsonl")
VALID_STATES = {"alpha", "beta", "stable", "deprecated"}

_SHORTCODE = re.compile(r"\{\{<\s*feature-state\s+(.*?)\s*>\}\}")
_ARG = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([\w-]+)\})?\s*#*\s*$")
_DOC_LINK = re.compile(r"\]\((/docs/[^)\s#]+)")


@dataclass(frozen=True)
class Label:
    id: str  # page path + anchor + index within that section
    kind: str  # "hardcoded" (stage typed by hand, can go stale) | "gate_lookup" (read from gate file)
    state: str | None  # hardcoded only
    for_k8s_version: str | None  # hardcoded only; "1.8" (leading v removed); None = site default
    gate_name: str | None  # gate_lookup only
    shortcode: str  # exact text as written
    page_path: str  # repo-relative
    page_url: str
    page_title: str | None
    line: int
    heading: str | None
    anchor: str | None
    section_text: str
    doc_links: list[str]
    page_doc_links: list[str]  # internal links anywhere on the page (weaker linking signal)
    source_url: str  # GitHub permalink pinned to a commit, with line
    issues: list[str] = field(default_factory=list)


def slugify(heading: str) -> str:
    """Approximation of Hugo's default heading id: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"[`*_\[\]()]", "", heading).lower()
    text = re.sub(r"[^\w\s-]", "", text).strip()
    return re.sub(r"\s+", "-", text)


def page_url(rel_to_docs: Path) -> str:
    """concepts/policy/resource-quotas.md -> https://kubernetes.io/docs/concepts/policy/resource-quotas/"""
    parts = list(rel_to_docs.with_suffix("").parts)
    if parts and parts[-1] in ("_index", "index"):
        parts = parts[:-1]
    return "https://kubernetes.io/docs/" + "/".join(parts) + ("/" if parts else "")


def extract_labels(text: str, page_path: str, rel_to_docs: Path, sha: str) -> list[Label]:
    meta = frontmatter.loads(text).metadata
    title = str(meta["title"]) if meta.get("title") else None
    lines = text.splitlines()
    page_links = sorted(set(_DOC_LINK.findall(text)))

    # pass 1: headings (outside code fences) -> list of (line_no, heading, anchor)
    headings: list[tuple[int, str, str]] = []
    fence = False
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        m = _HEADING.match(line)
        if m:
            headings.append((i, m.group(2), m.group(3) or slugify(m.group(2))))

    # pass 2: shortcodes
    labels: list[Label] = []
    per_section: dict[str, int] = {}
    fence = False
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        for m in _SHORTCODE.finditer(line):
            args = dict(_ARG.findall(m.group(1)))
            above = [h for h in headings if h[0] <= i]
            sec_line, heading, anchor = above[-1] if above else (1, None, None)
            later = [h[0] for h in headings if h[0] > sec_line]
            sec_end = (later[0] - 1) if later else len(lines)
            section = "\n".join(lines[sec_line - 1 : sec_end])
            key = f"{page_path}#{anchor or ''}"
            n = per_section[key] = per_section.get(key, 0) + 1

            issues: list[str] = []
            gate = args.get("feature_gate_name")
            state = args.get("state")
            version = args.get("for_k8s_version")
            if version:
                version = version.lstrip("v")
            if gate:
                kind = "gate_lookup"
            else:
                kind = "hardcoded"
                if state not in VALID_STATES:
                    issues.append(f"unexpected state {state!r}")
                if version is None:
                    issues.append("no for_k8s_version: defaults to the docs site version")
            labels.append(
                Label(
                    id=f"{key}:{n}",
                    kind=kind,
                    state=state if kind == "hardcoded" else None,
                    for_k8s_version=version if kind == "hardcoded" else None,
                    gate_name=gate,
                    shortcode=m.group(0),
                    page_path=page_path,
                    page_url=page_url(rel_to_docs),
                    page_title=title,
                    line=i,
                    heading=heading,
                    anchor=anchor,
                    section_text=section,
                    doc_links=sorted(set(_DOC_LINK.findall(section))),
                    page_doc_links=page_links,
                    source_url=f"https://github.com/kubernetes/website/blob/{sha}/{page_path}#L{i}",
                    issues=issues,
                )
            )
    return labels


def parse_all(repo_root: Path = WEBSITE_REPO) -> list[Label]:
    sha = repo_sha(repo_root)
    docs = repo_root / DOCS_DIR
    labels: list[Label] = []
    for p in sorted(docs.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "feature-state" not in text:
            continue
        labels += extract_labels(text, p.relative_to(repo_root).as_posix(), p.relative_to(docs), sha)
    return labels


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    labels = parse_all()
    SNAPSHOT.parent.mkdir(exist_ok=True)
    with SNAPSHOT.open("w") as f:
        for lab in labels:
            f.write(json.dumps(asdict(lab), ensure_ascii=False) + "\n")
    hard = [lab for lab in labels if lab.kind == "hardcoded"]
    log.info("labels: %d (hardcoded %d, gate_lookup %d) -> %s", len(labels), len(hard), len(labels) - len(hard), SNAPSHOT)
    log.info("pages with labels: %d", len({lab.page_path for lab in labels}))
    log.info("hardcoded without version: %d", sum(lab.for_k8s_version is None for lab in hard))
    for lab in labels:
        for issue in lab.issues:
            log.warning("%s:%d %s", lab.page_path, lab.line, issue)


if __name__ == "__main__":
    main()
