import math

import pytest

from lineage.agent.local_embeddings import DIM, MAX_CHUNK_TOKENS, pack, unpack
from lineage.search.chunks import build_chunks, load_chunks, parent_of, permalink, section_first_line, split_lines
from lineage.search.corpus import Passage

words = lambda s: len(s.split())  # noqa: E731  (a stand-in tokenizer: one word is one token)


def test_split_lines_respects_the_budget_keeps_line_numbers_and_overlaps():
    lines = [f"line{i} " + "w " * 9 for i in range(30)]  # 11 tokens per line
    pieces = split_lines(lines, words, max_tokens=50, overlap=12)
    assert len(pieces) > 1 and all(words(t) <= 50 for t, _, _ in pieces)
    assert pieces[0][1] == 0 and pieces[-1][2] == 29
    for (_, a1, b1), (_, a2, b2) in zip(pieces, pieces[1:]):
        assert a2 <= b1 + 1 and a2 > a1  # contiguous or overlapping, always moving forward
    covered = {i for _, a, b in pieces for i in range(a, b + 1)}
    assert covered == set(range(30))  # nothing is dropped


def test_a_single_huge_line_is_split_at_sentences_then_words():
    sentences = " ".join(f"Sentence number {i} says something rather long here." for i in range(40))
    pieces = split_lines([sentences], words, max_tokens=30)
    assert len(pieces) > 3 and all(words(t) <= 30 for t, _, _ in pieces) and all(a == b == 0 for _, a, b in pieces)
    no_spaces = ["x" * 5 + " " + " ".join(f"w{i}" for i in range(100))]
    assert all(words(t) <= 20 for t, _, _ in split_lines(no_spaces, words, max_tokens=20))


def test_short_input_is_one_piece_and_blank_input_is_none():
    assert split_lines(["a b c", "d e"], words, max_tokens=50) == [("a b c\nd e", 0, 1)]
    assert split_lines(["", "  "], words, max_tokens=50) == []


def test_section_first_line_and_permalink():
    lab = {"section_text": "## Heading\n\ntext\n{{< feature-state state=\"beta\" >}}\nmore", "shortcode": "{{< feature-state state=\"beta\" >}}", "line": 194}
    assert section_first_line(lab) == 191  # the label is the 4th line of the section
    assert section_first_line({**lab, "shortcode": "absent"}) is None
    assert permalink("https://github.com/o/r/blob/abc/f.md#L194", 191, 204) == "https://github.com/o/r/blob/abc/f.md#L191-L204"
    assert permalink("https://github.com/o/r/blob/abc/f.md", 7, 7).endswith("#L7")


def test_parent_of_maps_passages_to_graph_nodes():
    p = lambda i: Passage(i, "t", None)  # noqa: E731
    assert parent_of(p("gate:Foo")) == "FeatureGate:Foo" and parent_of(p("kep:sig-x/1-y")) == "KEP:sig-x/1-y" and parent_of(p("sig:sig-node")) == "SIG:sig-node"
    assert parent_of(p("owners:kubernetes:pkg/volume:137")) == "CodePath:kubernetes:pkg/volume" and parent_of(p("team:kubernetes:x")) == "Team:kubernetes:x"
    assert parent_of(p("doc:whatever")) is None


def test_pack_roundtrip_keeps_cosine_ranking():
    v = [math.sin(i) for i in range(DIM)]
    n = math.sqrt(sum(x * x for x in v))
    v = [x / n for x in v]
    back = unpack(pack(v))
    assert len(back) == DIM and abs(sum(a * b for a, b in zip(v, back)) - 1.0) < 1e-3 and len(pack(v)) < 2200


def test_long_generated_passage_is_split_and_keeps_its_source():
    long = Passage("gate:Big", " ".join(["alpha beta gamma."] * 400), "https://example/x")
    chunks = build_chunks(words, passages=[long], snap=__import__("pathlib").Path("/nonexistent-snap"))
    assert len(chunks) > 1 and all(c.source_url == "https://example/x" and c.parent == "FeatureGate:Big" and words(c.text) <= MAX_CHUNK_TOKENS for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)


def test_the_committed_chunk_file_is_complete_and_within_budget():
    chunks = load_chunks()
    if not chunks:
        pytest.skip("snapshot/chunks.jsonl not built (uv run python -m lineage.search.chunks)")
    assert len({c.id for c in chunks}) == len(chunks) and all(c.vec and len(unpack(c.vec)) == DIM for c in chunks)
    assert all(len(c.text.split()) < MAX_CHUNK_TOKENS for c in chunks)  # words < tokens, so this is a loose guard; the builder counts real tokens
    docs = [c for c in chunks if c.kind == "doc"]
    assert docs and all(c.line_start and c.line_end and f"#L{c.line_start}" in c.source_url for c in docs)


def test_local_embedder_is_deterministic_and_ranks_sensibly():
    from pathlib import Path

    from lineage.agent.local_embeddings import DEFAULT_CACHE, LocalEmbedder

    if not Path(DEFAULT_CACHE).exists():
        pytest.skip("local embedding model not downloaded")
    e = LocalEmbedder()
    a, b = e.embed_documents(["The job controller approvers are listed in OWNERS.", "Bananas are yellow fruit."])
    q = e.embed_query("who approves changes to the job controller")
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))  # noqa: E731
    assert len(a) == DIM and dot(q, a) > dot(q, b)
    assert e.embed_documents(["same text"]) == e.embed_documents(["same text"])


# --- FalkorDB vector index (needs the running database and the local model) ---------------------------------------------
from tests.test_loader import graph  # noqa: E402,F401


@pytest.fixture
def chunk_graph(graph, monkeypatch):  # noqa: F811
    from pathlib import Path

    from lineage.agent.local_embeddings import DEFAULT_CACHE, LocalEmbedder
    from lineage.graph import loader

    real = Path("snapshot/chunks.jsonl")
    if not real.exists() or not Path(DEFAULT_CACHE).exists():
        pytest.skip("chunks or local model not available")
    monkeypatch.setattr(loader, "CHUNKS_SNAPSHOT", real)
    loader.load_all(graph)
    return graph, LocalEmbedder()


def test_vector_index_returns_the_right_chunks_and_loading_is_idempotent(chunk_graph):
    from lineage.graph.loader import load_all
    from lineage.search.vector import vector_search

    g, emb = chunk_graph
    n = g.ro_query("MATCH (c:Chunk) RETURN count(c)").result_set[0][0]
    assert n > 2000 and g.ro_query("MATCH ()-[e:CHUNK_OF]->() WHERE e.recorded_at IS NULL OR e.extracted_by IS NULL OR e.source_url IS NULL RETURN count(e)").result_set[0][0] == 0
    hits = vector_search(g, emb.embed_query("What design decision introduced LocalStorageCapacityIsolation?"), 5)
    assert "gate:LocalStorageCapacityIsolation" in [h["id"] for h in hits[:3]] and all(0 <= h["similarity"] <= 1 for h in hits)
    assert hits == sorted(hits, key=lambda h: -h["similarity"])
    doc = vector_search(g, emb.embed_query("quota for local ephemeral storage in resource quotas"), 5)
    d = next(h for h in doc if h["kind"] == "doc")
    assert d["source_url"].endswith(f"#L{d['line_start']}-L{d['line_end']}") or d["line_start"] == d["line_end"]
    assert load_all(g)["Chunk"] == n  # MERGE: a second load adds nothing


def test_search_text_tool_expands_hits_along_the_graph_and_refuses_off_topic(chunk_graph):
    from lineage.agent.tools import ToolBox

    g, emb = chunk_graph
    tb = ToolBox(g, embedder=emb)
    b = tb.search_text("Which design document introduced the LocalStorageCapacityIsolation feature gate?")
    types = {e["type"] for e in b["evidence"]}
    assert b["status"] == "ok" and {"Chunk", "INTRODUCES"} <= types
    assert all(e["source_url"] for e in b["evidence"] if e["type"] == "Chunk")
    off = tb.search_text("who won the world cup in 2022")
    assert off["status"] == "insufficient_evidence" and off["evidence"] == []


def test_falkor_retriever_has_the_same_shape_as_the_other_baselines(chunk_graph):
    from lineage.search.vector import FalkorRetriever

    g, emb = chunk_graph
    hits = FalkorRetriever(g, emb).search("Who chairs sig-storage?", 5)
    assert len(hits) == 5 and all(hasattr(p, "text") and p.source_url is not None for p, _ in hits)
