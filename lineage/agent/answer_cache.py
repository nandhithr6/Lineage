"""Saved answers for the demo questions, so the demo never depends on an LLM quota.

An entry is a full AskResponse (answer, citations, evidence with source URLs, path, tool trace) produced by the REAL agent against the REAL graph and stored in
`snapshot/demo_answers.jsonl`. It is keyed by the normalised question plus a fingerprint of the data snapshot and the detector, so an answer saved for one version of
the data is never served for another: change the data and the entry simply stops matching (the question then goes to the model).
Provenance is untouched: the stored evidence is exactly what the tools returned when the answer was generated, and the response says it is a saved answer.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

CACHE_PATH = Path("snapshot/demo_answers.jsonl")
SNAPSHOT_DIR = Path("snapshot")
# Not part of the fingerprint: the cache itself, and the text-search chunks/embeddings, which only the free-text search tool reads (the saved demo answers come from the
# structured tools, so re-embedding must not silently throw them away).
FINGERPRINT_EXCLUDE = {"demo_answers.jsonl", "chunks.jsonl"}
CACHE_VERSION = "1"  # bump when the answer format or the agent's rules change in a way that makes old answers misleading

# The demo-script questions (docs/demo_script.md) and the Ask page's example buttons. Keep this list short: every entry costs LLM calls to build.
HERO_QUESTION = "Why is the Resource Quotas page stale?"  # cached both ways (graph and plain retrieval) for the side-by-side demo
DEMO_QUESTIONS = [
    "Why is the Resource Quotas page stale?",
    "Is the 'stable since v1.27' label in the CronJob time zones section correct?",
    "Who owned pkg/controller/job in 1.30 and who owns it now?",
    "Who officially owns pkg/volume and who actually reviews it?",
    "What design decision introduced LocalStorageCapacityIsolation?",
    "Which docs pages look out of date?",
]
# Answered in code with no model and no saved entry (the question is resolved before the model is called), so they work with no key and need no cache.
REFUSAL_DEMOS = [
    "What is the stage of the LocalStorageCapacityIsolatio gate?",  # a typo: unknown_entity with "did you mean", no traversal
    "Who will approve pkg/controller/job in release 1.45?",  # the future: unknown_entity (release 1.45 does not exist in the graph)
]


def normalise(question: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9./_\- ]+", " ", question.lower())).strip()


def snapshot_id(snapshot_dir: Path = SNAPSHOT_DIR) -> str:
    """Fingerprint of everything an answer can depend on: every snapshot file except the cache itself, plus the detector version."""
    from lineage.detect.drift import DETECTOR_VERSION

    h = hashlib.sha256(f"cache{CACHE_VERSION}|detector{DETECTOR_VERSION}".encode())
    for f in sorted(snapshot_dir.glob("*")):
        if f.is_file() and f.name not in FINGERPRINT_EXCLUDE:
            h.update(f.name.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:16]


def cache_key(question: str, sid: str, mode: str = "graph") -> str:
    """The graph answer keeps the original key format (saved entries stay valid); the retrieval answer to the same question gets its own key."""
    return hashlib.sha256(f"{sid}|{normalise(question)}".encode() if mode == "graph" else f"{sid}|{mode}|{normalise(question)}".encode()).hexdigest()[:24]


def load(path: Path = CACHE_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["key"]] = row
    return out


class AnswerCache:
    def __init__(self, path: Path = CACHE_PATH, snapshot_dir: Path = SNAPSHOT_DIR):
        self.path, self.snapshot_dir = path, snapshot_dir
        self._sid: str | None = None
        self._rows: dict[str, dict] | None = None

    @property
    def sid(self) -> str:
        if self._sid is None:
            self._sid = snapshot_id(self.snapshot_dir)
        return self._sid

    def get(self, question: str, mode: str = "graph") -> dict | None:
        if self._rows is None:
            self._rows = load(self.path)
        row = self._rows.get(cache_key(question, self.sid, mode))
        return row["response"] | {"cached": True, "cached_at": row["generated_at"], "snapshot_id": row["snapshot_id"]} if row else None

    def put(self, question: str, response: dict, provider: str | None = None, mode: str = "graph") -> dict:
        row = {"key": cache_key(question, self.sid, mode), "mode": mode, "question": question, "snapshot_id": self.sid, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "provider": provider, "response": response}
        rows = load(self.path)
        rows[row["key"]] = row
        self.path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows.values()))
        self._rows = rows
        return row

    def prune(self) -> int:
        """Drop entries saved for other snapshots (they are never served and only make the committed file grow)."""
        rows = load(self.path)
        keep = {k: r for k, r in rows.items() if r["snapshot_id"] == self.sid}
        self.path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in keep.values()))
        self._rows = keep
        return len(rows) - len(keep)

    def stale_entries(self) -> list[str]:
        """Questions stored for a different snapshot (they will not be served)."""
        return [r["question"] for r in load(self.path).values() if r["snapshot_id"] != self.sid]
