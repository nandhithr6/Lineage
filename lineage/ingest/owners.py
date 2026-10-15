"""OWNERS files (who may approve/review changes in a folder), their aliases, and history sampled at release tags.

Run: `uv run python -m lineage.ingest.owners` -> writes snapshot/owners_edges.jsonl, team_members.jsonl,
codepaths.jsonl, release_dates.jsonl.

How OWNERS works (spec: https://www.kubernetes.dev/docs/guide/owners/):
  * an OWNERS file applies to its folder and everything below it, unless `options: {no_parent_owners: true}`
  * entries can be GitHub handles or ALIASES defined in OWNERS_ALIASES (a repo-level file); we keep aliases as Team nodes
    (prefixed with the repo, because both repos define `release-engineering-approvers`) and let the graph resolve members
  * `filters` maps a regex over the file path to its own owner lists; we keep the regex on the edge

History (DECISIONS.md): sampled at release tags v1.30.0..v1.37.0 plus HEAD (labelled 1.38-dev), then collapsed into
intervals. valid_from = first SAMPLED release where the fact was seen (the true start may be earlier); valid_to = the first
sampled release where it was gone, or null if still present at HEAD. source_url points at the LAST sample where it was still true. Website OWNERS are sampled at HEAD only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

log = logging.getLogger(__name__)

K8S_REPO = Path("data/raw/kubernetes")
WEBSITE_REPO = Path("data/raw/website")
SIGS_SNAPSHOT = Path("snapshot/sigs.jsonl")
SCOPE_SIGS = ("sig-storage", "sig-node", "sig-apps")
HEAD_NUM, HEAD_VERSION = 138, "1.38-dev"
K8S_REFS = [(n, f"1.{n - 100}", f"v1.{n - 100}.0") for n in range(130, 138)] + [(HEAD_NUM, HEAD_VERSION, "HEAD")]
ROLES = {"approvers": "APPROVER_OF", "reviewers": "REVIEWER_OF", "emeritus_approvers": "EMERITUS_APPROVER_OF"}
_RAW_URL = re.compile(r"^https://raw\.githubusercontent\.com/([\w.-]+)/([\w.-]+)/[\w.-]+/(.*?)/?OWNERS$")


@dataclass
class OwnersFile:
    no_parent_owners: bool = False
    # entries: (role, entry, filter_regex or "") with entry lowercased
    entries: list[tuple[str, str, str]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    has_filters: bool = False
    issues: list[str] = field(default_factory=list)


def _names(values: object, issues: list[str], where: str) -> list[str]:
    out = []
    for v in values or []:
        if isinstance(v, str) and v.strip():
            out.append(v.strip().lstrip("@").lower())
        else:
            issues.append(f"{where}: ignored non-string entry {v!r}")
    return out


def parse_owners(text: str) -> OwnersFile:
    f = OwnersFile()
    try:
        y = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        f.issues.append(f"unparseable YAML: {str(e)[:60]}")
        return f
    if not isinstance(y, dict):
        f.issues.append("top level is not a mapping")
        return f
    f.no_parent_owners = bool((y.get("options") or {}).get("no_parent_owners", False))
    f.labels = [str(x) for x in y.get("labels") or []]
    for role in ROLES:
        f.entries += [(role, n, "") for n in _names(y.get(role), f.issues, role)]
    for regex, body in (y.get("filters") or {}).items():
        f.has_filters = True
        for role in ROLES:
            f.entries += [(role, n, str(regex)) for n in _names((body or {}).get(role), f.issues, f"filters[{regex}].{role}")]
    return f


def parse_aliases(text: str) -> dict[str, list[str]]:
    y = yaml.safe_load(text) or {}
    return {str(k).lower(): [str(m).lstrip("@").lower() for m in (v or [])] for k, v in (y.get("aliases") or {}).items()}


def collapse(presence: dict[int, set], order: list[int]) -> dict[object, list[tuple[int, int | None]]]:
    """presence: sampled release -> set of keys present. Returns key -> [(valid_from, valid_to)] runs; valid_to None = still present."""
    out: dict[object, list[tuple[int, int | None]]] = defaultdict(list)
    keys = set().union(*presence.values()) if presence else set()
    for key in keys:
        start = None
        for i, num in enumerate(order):
            here = key in presence.get(num, set())
            if here and start is None:
                start = num
            if not here and start is not None:
                out[key].append((start, num))
                start = None
        if start is not None:
            out[key].append((start, None))
    return out


# --- git access ----------------------------------------------------------------------------------------

def git_read_many(repo: Path, ref: str, paths: Iterable[str]) -> dict[str, str | None]:
    """Read files at a ref with one `git cat-file --batch`. Missing files map to None. Blobless clones fetch blobs lazily."""
    paths = list(paths)
    stdin = "".join(f"{ref}:{p}\n" for p in paths).encode()
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input=stdin, capture_output=True, check=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"})
    data, pos, out = proc.stdout, 0, {}
    for p in paths:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].decode()
        pos = nl + 1
        if header.endswith(" missing"):
            out[p] = None
            continue
        size = int(header.split()[2])
        out[p] = data[pos : pos + size].decode("utf-8", errors="replace")
        pos += size + 1
    return out


def git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def chain(directory: str) -> list[str]:
    """'a/b/c' -> ['', 'a', 'a/b', 'a/b/c'] (root first)."""
    parts = [p for p in directory.split("/") if p]
    return [""] + ["/".join(parts[: i + 1]) for i in range(len(parts))]


def owners_path(directory: str) -> str:
    return f"{directory}/OWNERS" if directory else "OWNERS"


def scope_dirs_k8s(sigs_snapshot: Path = SIGS_SNAPSHOT) -> dict[str, list[str]]:
    """kubernetes/kubernetes directories named by the subprojects of the in-scope SIGs: {dir: [sig ids]}."""
    scope: dict[str, list[str]] = defaultdict(list)
    for line in sigs_snapshot.read_text().splitlines():
        sig = json.loads(line)
        if sig["id"] not in SCOPE_SIGS:
            continue
        for sp in sig["subprojects"]:
            for url in sp["owners_urls"]:
                m = _RAW_URL.match(url)
                if m and (m.group(1), m.group(2)) == ("kubernetes", "kubernetes"):
                    scope[m.group(3)].append(sig["id"])
    return scope


def docs_dirs(labels_snapshot: Path = Path("snapshot/doc_labels.jsonl")) -> set[str]:
    return {str(Path(json.loads(l)["page_path"]).parent) for l in labels_snapshot.read_text().splitlines()}


# --- sampling ------------------------------------------------------------------------------------------

def sample_repo(repo: Path, repo_name: str, refs: list[tuple[int, str, str]], dirs: set[str]):
    """Read OWNERS + OWNERS_ALIASES at each ref. Returns (owners_by_ref, aliases_by_ref, shas, dates)."""
    all_dirs = sorted({d for x in dirs for d in chain(x)})
    owners: dict[int, dict[str, OwnersFile]] = {}
    aliases: dict[int, dict[str, list[str]]] = {}
    shas, dates = {}, {}
    for num, version, ref in refs:
        shas[num] = git_out(repo, "rev-parse", f"{ref}^{{commit}}")
        dates[num] = git_out(repo, "log", "-1", "--format=%cI", ref)
        files = git_read_many(repo, ref, [owners_path(d) for d in all_dirs] + ["OWNERS_ALIASES"])
        owners[num] = {d: parse_owners(files[owners_path(d)]) for d in all_dirs if files[owners_path(d)] is not None}
        aliases[num] = parse_aliases(files["OWNERS_ALIASES"]) if files["OWNERS_ALIASES"] else {}
        log.info("%s %s (%s): %d OWNERS files, %d aliases", repo_name, version, shas[num][:8], len(owners[num]), len(aliases[num]))
    return owners, aliases, shas, dates


def build_rows(repo_name: str, owners, aliases, shas, refs, order: list[int]):
    """Collapse sampled OWNERS into edge intervals. Returns (owner_rows, member_rows)."""
    url_repo = {"kubernetes": "kubernetes/kubernetes", "website": "kubernetes/website"}[repo_name]
    presence: dict[int, set] = {}
    url_at: dict[tuple, str] = {}
    members_presence: dict[int, set] = {}
    for num in order:
        present, mem = set(), set()
        for d, f in owners.get(num, {}).items():
            for role, entry, flt in f.entries:
                kind = "team" if entry in aliases.get(num, {}) else "person"
                key = (d, role, kind, entry, flt)
                present.add(key)
                url_at[(key, num)] = f"https://github.com/{url_repo}/blob/{shas[num]}/{owners_path(d)}"
        for alias, people in aliases.get(num, {}).items():
            mem |= {(alias, p) for p in people}
        presence[num], members_presence[num] = present, mem
    owner_rows = []
    for key, runs in collapse(presence, order).items():
        d, role, kind, entry, flt = key
        for start, end in runs:
            last = order[order.index(end) - 1] if end is not None else order[-1]  # last sample where it was still true
            owner_rows.append({"repo": repo_name, "dir": d, "role": ROLES[role], "subject_kind": kind,
                               "subject": f"{repo_name}:{entry}" if kind == "team" else entry, "filter": flt,
                               "valid_from": start, "valid_to": end, "source_url": url_at[(key, last)]})
    member_rows = []
    for (alias, person), runs in collapse(members_presence, order).items():
        for start, end in runs:
            last = order[order.index(end) - 1] if end is not None else order[-1]
            member_rows.append({"team": f"{repo_name}:{alias}", "handle": person, "valid_from": start, "valid_to": end,
                                "source_url": f"https://github.com/{url_repo}/blob/{shas[last]}/OWNERS_ALIASES"})
    return sorted(owner_rows, key=lambda r: json.dumps(r, sort_keys=True)), sorted(member_rows, key=lambda r: json.dumps(r, sort_keys=True))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = Path("snapshot")
    all_owner_rows, all_member_rows, codepaths = [], [], []
    for repo, name, refs, dirs in (
        (K8S_REPO, "kubernetes", K8S_REFS, set(scope_dirs_k8s())),
        (WEBSITE_REPO, "website", [(HEAD_NUM, HEAD_VERSION, "HEAD")], docs_dirs()),
    ):
        owners, aliases, shas, dates = sample_repo(repo, name, refs, dirs)
        order = [n for n, _, _ in refs]
        o, m = build_rows(name, owners, aliases, shas, refs, order)
        all_owner_rows += o
        all_member_rows += m
        head = owners[HEAD_NUM]
        for d in sorted({x for dd in dirs for x in chain(dd)}):
            f = head.get(d)
            codepaths.append({"repo": name, "dir": d, "parent": chain(d)[-2] if d else None, "has_owners": f is not None,
                              "no_parent_owners": bool(f and f.no_parent_owners), "has_filters": bool(f and f.has_filters),
                              "labels": f.labels if f else [],
                              "source_url": f"https://github.com/kubernetes/{name}/blob/{shas[HEAD_NUM]}/{owners_path(d)}" if f else None})
        for num, f_by_dir in owners.items():
            for d, f in f_by_dir.items():
                for issue in f.issues:
                    log.warning("%s %s %s: %s", name, refs[[r[0] for r in refs].index(num)][1], d or "<root>", issue)
    tags = git_out(K8S_REPO, "tag", "-l", "v1.*.0").split()
    release_dates = [{"version": t[1:-2], "num": int(t[1:-2].split(".")[0]) * 100 + int(t[1:-2].split(".")[1]),
                      "date": git_out(K8S_REPO, "log", "-1", "--format=%cI", t)}
                     for t in tags if re.fullmatch(r"v1\.\d+\.0", t)]
    for fname, rows in (("owners_edges", all_owner_rows), ("team_members", all_member_rows), ("codepaths", codepaths),
                        ("release_dates", sorted(release_dates, key=lambda r: r["num"]))):
        (out / f"{fname}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        log.info("%s: %d rows", fname, len(rows))


if __name__ == "__main__":
    main()
