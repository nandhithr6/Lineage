"""Chunks: the retrieval corpus split into pieces of at most ~400 tokens, each keeping its source URL and (for docs) its line range, with an embedding.

Docs sections are split by LINES, so every chunk can point at the exact lines it came from (`...#L194-L231` on a commit-pinned GitHub URL). Generated passages
(gate stages, KEP metadata, OWNERS per release, teams) are normally far below the limit; any that exceeds it is split at sentence boundaries and keeps its source URL (the
text is built by Lineage from structured data, so it has no line range of its own).

    uv run python -m lineage.search.chunks          # build snapshot/chunks.jsonl (embeds every chunk locally, about 1-2 minutes)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from lineage.agent.local_embeddings import MAX_CHUNK_TOKENS, MODEL, LocalEmbedder, pack
from lineage.link.linker import read_jsonl
from lineage.search.corpus import SNAP, Passage, build_corpus

log = logging.getLogger(__name__)
CHUNKS_PATH = SNAP / "chunks.jsonl"
OVERLAP_TOKENS = 40


@dataclass
class Chunk:
    id: str
    text: str
    source_url: str | None
    kind: str  # doc | label | gate | kep | sig | team | owners
    parent: str | None  # graph node this chunk is about, "Label:key" (DocSection, FeatureGate, KEP, SIG, Team, CodePath, Claim)
    line_start: int | None = None
    line_end: int | None = None
    vec: str | None = None  # base64 float16, set when embedded


def split_lines(lines: list[str], count: Callable[[str], int], max_tokens: int = MAX_CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS,
                prefix_tokens: int = 0) -> list[tuple[str, int, int]]:
    """Greedy split of `lines` into (text, first_line_index, last_line_index) pieces of at most `max_tokens` (counting `prefix_tokens` that every piece will carry).

    Consecutive pieces overlap by up to `overlap` tokens of whole lines so a sentence cut at a boundary is still whole in one of them. A single line longer than the
    budget is split at sentence ends, then at words; those pieces share that line's index."""
    budget = max_tokens - prefix_tokens
    units: list[tuple[str, int]] = []  # (text, line index)
    for i, line in enumerate(lines):
        if count(line) <= budget:
            units.append((line, i))
            continue
        piece: list[str] = []
        for sent in re.split(r"(?<=[.!?])\s+", line):
            for word_group in ([sent] if count(sent) <= budget else _by_words(sent, count, budget)):
                if piece and count(" ".join(piece + [word_group])) > budget:
                    units.append((" ".join(piece), i))
                    piece = []
                piece.append(word_group)
        if piece:
            units.append((" ".join(piece), i))
    out: list[tuple[str, int, int]] = []
    cur: list[tuple[str, int]] = []
    cur_tokens = 0
    for text, idx in units:
        n = count(text) + 1
        if cur and cur_tokens + n > budget:
            out.append(("\n".join(t for t, _ in cur), cur[0][1], cur[-1][1]))
            keep: list[tuple[str, int]] = []
            kept = 0
            for t, i in reversed(cur):  # carry the tail of the previous piece forward
                if kept + count(t) + 1 > overlap:
                    break
                keep.insert(0, (t, i))
                kept += count(t) + 1
            while keep and kept + n > budget:  # the carried tail must still leave room for the next unit
                kept -= count(keep[0][0]) + 1
                keep.pop(0)
            cur, cur_tokens = keep, kept
        cur.append((text, idx))
        cur_tokens += n
    if cur and any(t.strip() for t, _ in cur):
        out.append(("\n".join(t for t, _ in cur), cur[0][1], cur[-1][1]))
    return out


def _by_words(text: str, count: Callable[[str], int], budget: int) -> list[str]:
    words, out, cur = text.split(), [], []
    for w in words:
        if cur and count(" ".join(cur + [w])) > budget:
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


def section_first_line(lab: dict) -> int | None:
    """1-based line in the docs file where `section_text` starts: the label's own line minus its offset inside the section."""
    for off, line in enumerate(lab["section_text"].split("\n")):
        if lab["shortcode"] in line:
            return lab["line"] - off
    return None


def permalink(url: str | None, start: int, end: int) -> str | None:
    return f"{url.split('#')[0]}#L{start}" + (f"-L{end}" if end != start else "") if url else None


PARENT_KIND = {"label": "Claim", "gate": "FeatureGate", "kep": "KEP", "sig": "SIG", "team": "Team"}


def parent_of(p: Passage) -> str | None:
    kind, _, rest = p.id.partition(":")
    if kind == "owners":
        repo, _, tail = rest.partition(":")
        return f"CodePath:{repo}:{tail.rsplit(':', 1)[0]}"
    return f"{PARENT_KIND[kind]}:{rest}" if kind in PARENT_KIND else None


def build_chunks(count: Callable[[str], int], snap: Path = SNAP, passages: list[Passage] | None = None) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: set[str] = set()
    for lab in (read_jsonl(snap / "doc_labels.jsonl") if (snap / "doc_labels.jsonl").exists() else []):  # one chunked entry per docs section (the first label seen), full text, no truncation
        sec = f"{lab['page_path']}#{lab['anchor']}"
        if sec in seen:
            continue
        seen.add(sec)
        header = f"Docs page \"{lab['page_title']}\", section \"{lab['heading']}\". "
        first = section_first_line(lab)
        lines = lab["section_text"].split("\n")
        for n, (text, a, b) in enumerate(split_lines(lines, count, prefix_tokens=count(header))):
            if not text.strip():
                continue
            s, e = (first + a, first + b) if first else (None, None)
            chunks.append(Chunk(f"doc:{sec}:c{n}", header + text, permalink(lab["source_url"], s, e) if s else lab["source_url"], "doc", f"DocSection:{sec}", s, e))
    for p in (passages if passages is not None else build_corpus()):
        if p.id.startswith("doc:"):
            continue
        kind = p.id.split(":", 1)[0]
        pieces = split_lines([p.text], count) if count(p.text) > MAX_CHUNK_TOKENS else [(p.text, 0, 0)]
        for n, (text, _, _) in enumerate(pieces):
            chunks.append(Chunk(p.id if len(pieces) == 1 else f"{p.id}:c{n}", text, p.source_url, kind, parent_of(p)))
    return chunks


def load_chunks(path: Path = CHUNKS_PATH) -> list[Chunk]:
    return [Chunk(**r) for r in read_jsonl(path)] if path.exists() else []


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(CHUNKS_PATH))
    args = ap.parse_args()
    emb = LocalEmbedder()
    chunks = build_chunks(emb.count_tokens)
    too_long = [c for c in chunks if emb.count_tokens(c.text) > MAX_CHUNK_TOKENS + 8]
    log.info("%d chunks (%d over the %d-token budget)", len(chunks), len(too_long), MAX_CHUNK_TOKENS)
    vecs = emb.embed_documents([c.text for c in chunks])
    for c, v in zip(chunks, vecs):
        c.vec = pack(v)
    Path(args.out).write_text("".join(json.dumps(asdict(c), ensure_ascii=False) + "\n" for c in chunks))
    log.info("wrote %s (model %s)", args.out, MODEL)


if __name__ == "__main__":
    main()
