"""Does an embedding signal raise confident-link coverage without lowering precision? Tune on page-half A, report on the held-out half B (same protocol as score_links).

The semantic signal belongs to the linker's "text" family, so it can only strengthen a link that another family (name, links, KEP) also supports; it cannot make one confident alone.
Run: uv run python -m eval.tune_semantic
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

from eval.score_links import GATES, KEPS, LABELS, half, score
from lineage.agent.local_embeddings import LocalEmbedder, unpack
from lineage.link.linker import _SHORTCODE, Linker, read_jsonl
from lineage.search.chunks import load_chunks


class EmbeddedLinker(Linker):
    """score_links calls linker.link(text, ...) with no embedding; look the section's embedding up by its text instead."""

    def __init__(self, *a, section_by_text: dict[str, list[float]], **kw):
        super().__init__(*a, **kw)
        self.section_by_text = section_by_text

    def link(self, section_text, section_links, heading=None, page_links=None, since=None, section_embedding=None):
        return super().link(section_text, section_links, heading, page_links, since, self.section_by_text.get(section_text))


def main() -> None:
    gates, keps = read_jsonl(GATES), read_jsonl(KEPS)
    names = {g["name"] for g in gates}
    chunks = {c.id: c for c in load_chunks()}
    gate_emb = {n: unpack(chunks[f"gate:{n}"].vec) for n in names if f"gate:{n}" in chunks}
    labels = [x for x in read_jsonl(LABELS) if x["kind"] == "gate_lookup" and x["gate_name"] in names]
    emb = LocalEmbedder()
    stripped = [_SHORTCODE.sub(" ", lab["section_text"]) for lab in labels]  # exactly the text score_links hands to the linker
    vecs = emb.embed_documents([(f'Docs page "{lab["page_title"]}", section "{lab["heading"]}". ' + t)[:1800] for lab, t in zip(labels, stripped)])
    by_text = dict(zip(stripped, vecs))
    A = [x for x in labels if half(x["page_path"]) == "A"]
    B = [x for x in labels if half(x["page_path"]) == "B"]
    results = []
    for name_params, k, tag in (({"name_mention": 0.0, "name_bare": 0.0}, keps, "no name + KEP"), (None, keps, "full")):
        base = EmbeddedLinker(gates, {**(name_params or {}), "sem_max": 0.0}, k, gate_embeddings=gate_emb, section_by_text=by_text)
        print(f"\n== {tag}: baseline  A {score(A, base)}  B {score(B, base)}")
        grid = []
        for floor, scale, mx in itertools.product((0.62, 0.66, 0.70, 0.74), (0.08, 0.12, 0.18), (0.3, 0.5, 0.7)):
            lk = EmbeddedLinker(gates, {**(name_params or {}), "sem_floor": floor, "sem_scale": scale, "sem_max": mx}, k, gate_embeddings=gate_emb, section_by_text=by_text)
            a = score(A, lk)
            grid.append((a["confident_coverage"] if (a["confident_precision"] or 0) >= 0.95 else -1, floor, scale, mx, a))
        grid.sort(key=lambda r: -r[0])
        best = grid[0]
        print(f"   best on A (precision >= 0.95): floor {best[1]} scale {best[2]} max {best[3]} -> A {best[4]}")
        lk = EmbeddedLinker(gates, {**(name_params or {}), "sem_floor": best[1], "sem_scale": best[2], "sem_max": best[3]}, k, gate_embeddings=gate_emb, section_by_text=by_text)
        print(f"   held-out half B with those settings: {score(B, lk)}")
        results.append({"variant": tag, "baseline_A": score(A, base), "baseline_B": score(B, base), "best": {"sem_floor": best[1], "sem_scale": best[2], "sem_max": best[3]},
                        "tuned_A": best[4], "tuned_B": score(B, lk)})
    Path("eval/results/semantic_linker.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
