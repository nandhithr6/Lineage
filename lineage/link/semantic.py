"""Embeddings for the linker's optional semantic signal, read from snapshot/chunks.jsonl (BAAI/bge-base-en-v1.5), so linking needs no model at run time.

A docs section may be several chunks: its vector is the normalised mean of its chunk vectors. A gate's vector is its `gate:<name>` chunk.
Tuned on page-half A of the known-gate claims and checked on half B (eval/tune_semantic.py, eval/results/semantic_linker.json).
"""
from __future__ import annotations

import numpy as np

from lineage.agent.local_embeddings import unpack
from lineage.search.chunks import Chunk

# Settings chosen on half A with the gate name hidden (the regime of real hard-coded labels, which almost never name their gate).
TUNED = {"sem_floor": 0.74, "sem_scale": 0.08, "sem_max": 0.7}


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v)) or 1.0
    return v / n


def gate_embeddings(chunks: list[Chunk]) -> dict[str, list[float]]:
    by: dict[str, list[np.ndarray]] = {}
    for c in chunks:
        if c.kind == "gate" and c.vec:
            by.setdefault(c.parent.split(":", 1)[1], []).append(np.asarray(unpack(c.vec), dtype=np.float32))
    return {name: unit(np.mean(vs, axis=0)).tolist() for name, vs in by.items()}


def section_embeddings(chunks: list[Chunk], labels: list[dict]) -> dict[str, list[float]]:
    """claim id -> embedding of the docs section the label sits in."""
    by: dict[str, list[np.ndarray]] = {}
    for c in chunks:
        if c.kind == "doc" and c.vec:
            by.setdefault(c.parent.split(":", 1)[1], []).append(np.asarray(unpack(c.vec), dtype=np.float32))
    out = {}
    for lab in labels:
        vs = by.get(f"{lab['page_path']}#{lab['anchor']}")
        if vs:
            out[lab["id"]] = unit(np.mean(vs, axis=0)).tolist()
    return out
