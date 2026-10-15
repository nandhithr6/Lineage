import json
import logging

import httpx
import pytest

from lineage.agent import embeddings as E


def fake_post_factory(calls, status_seq=None):
    seq = list(status_seq or [])

    def fake_post(url, **kw):
        calls.append((url, kw))
        status = seq.pop(0) if seq else 200
        n = len(kw["json"]["requests"])
        body = {"embeddings": [{"values": [float(i), 1.0]} for i in range(n)]} if status == 200 else {"error": "x SECRETKEY"}
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    return fake_post


def make(tmp_path, sleeps, t=None, **kw):
    clock = (lambda: t[0]) if t else (lambda: 0.0)
    return E.GeminiEmbedder("SECRETKEY", "m", dim=2, cache_dir=tmp_path, sleep=sleeps.append, clock=clock, **kw)


def test_key_goes_in_a_header_never_in_the_url_and_urls_are_not_logged(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(E.httpx, "post", fake_post_factory(calls))
    make(tmp_path, []).embed(["a"], "RETRIEVAL_QUERY")
    url, kw = calls[0]
    assert "SECRETKEY" not in url and "key" not in (kw.get("params") or {}) and kw["headers"]["x-goog-api-key"] == "SECRETKEY"
    assert logging.getLogger("httpx").level >= logging.WARNING  # httpx would otherwise log full request URLs at INFO


def test_batches_cache_and_task_type(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(E.httpx, "post", fake_post_factory(calls))
    emb = make(tmp_path, [], batch=2)
    out = emb.embed(["a", "b", "c"], "RETRIEVAL_DOCUMENT")
    assert len(out) == 3 and len(calls) == 2  # 3 texts in batches of 2
    assert all(r["taskType"] == "RETRIEVAL_DOCUMENT" and r["outputDimensionality"] == 2 for r in calls[0][1]["json"]["requests"])
    again = make(tmp_path, [], batch=2)  # a new object reads the on-disk cache: no new calls
    assert again.embed(["a", "b", "c"], "RETRIEVAL_DOCUMENT") == out and len(calls) == 2 and again.stats["cache_hits"] == 3
    again.embed(["a"], "RETRIEVAL_QUERY")  # same text, other task type: a different embedding, so a new call
    assert len(calls) == 3


def test_rolling_minute_budget_makes_the_embedder_wait(monkeypatch, tmp_path):
    calls, sleeps, t = [], [], [0.0]
    monkeypatch.setattr(E.httpx, "post", fake_post_factory(calls))
    emb = make(tmp_path, sleeps, t, per_minute=4, batch=2)
    emb._sleep = lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s))
    emb.embed(["a", "b", "c", "d", "e", "f"], "RETRIEVAL_DOCUMENT")  # 6 texts but only 4 allowed per minute
    assert len(calls) == 3 and sleeps and sum(sleeps) >= 60


def test_429_waits_a_full_minute_then_succeeds_and_gives_up_eventually(monkeypatch, tmp_path):
    calls, sleeps = [], []
    monkeypatch.setattr(E.httpx, "post", fake_post_factory(calls, [429, 200]))
    make(tmp_path, sleeps).embed(["a"], "RETRIEVAL_DOCUMENT")
    assert 65.0 in sleeps
    monkeypatch.setattr(E.httpx, "post", fake_post_factory([], [429] * 10))
    with pytest.raises(E.EmbeddingError) as e:
        make(tmp_path / "x", [], max_retries=2).embed(["zzz"], "RETRIEVAL_DOCUMENT")
    assert "SECRETKEY" not in str(e.value)


def test_from_settings_requires_configuration():
    from lineage.config import load_settings
    with pytest.raises(E.EmbeddingError):
        E.GeminiEmbedder.from_settings(load_settings({}))
