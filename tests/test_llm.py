import json

import httpx
import pytest

from lineage.agent import llm as L
from lineage.config import load_settings


def resp(status=200, body=None, headers=None):
    return httpx.Response(status, json=body if body is not None else {}, headers=headers or {}, request=httpx.Request("POST", "https://x"))


OK = {"choices": [{"message": {"content": "hi", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": json.dumps({"a": 1})}}]}}]}


@pytest.fixture
def sleeps():
    return []


def make(sleeps, **kw):
    return L.OpenAICompatLLM("https://x/v1", "SECRETKEY", "m", "p", sleep=sleeps.append, **kw)


def script(monkeypatch, answers):
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(L.httpx, "post", fake_post)
    return calls


def test_success_parses_tool_calls_and_names_the_provider(monkeypatch, sleeps):
    calls = script(monkeypatch, [resp(200, OK)])
    r = make(sleeps).chat([{"role": "user", "content": "x"}], [{"name": "t", "description": "d", "parameters": {}}])
    assert r.tool_calls[0].name == "t" and r.tool_calls[0].arguments == {"a": 1} and r.provider == "p"
    body = calls[0][1]["json"]
    assert body["tools"][0]["function"]["name"] == "t" and body["tool_choice"] == "auto" and calls[0][1]["headers"]["Authorization"] == "Bearer SECRETKEY"


def test_no_tools_means_no_tools_field(monkeypatch, sleeps):
    calls = script(monkeypatch, [resp(200, OK)])
    make(sleeps).chat([{"role": "user", "content": "x"}], [])
    assert "tools" not in calls[0][1]["json"] and "tool_choice" not in calls[0][1]["json"]


def test_429_is_retried_with_backoff_then_succeeds(monkeypatch, sleeps):
    script(monkeypatch, [resp(429, {"error": "slow down"}), resp(503), resp(200, OK)])
    llm = make(sleeps)
    assert llm.chat([], []).text == "hi"
    assert llm.stats["retries"] == 2 and llm.stats["rate_limited"] == 1
    assert sleeps[0] >= 4 and max(sleeps) >= 8  # exponential backoff (plus a pacing wait added after the 429)


def test_retry_after_header_is_honoured(monkeypatch, sleeps):
    script(monkeypatch, [resp(429, headers={"retry-after": "7"}), resp(200, OK)])
    make(sleeps).chat([], [])
    assert sleeps[0] == 7.0


def test_after_a_429_the_pacing_interval_grows(monkeypatch, sleeps):
    script(monkeypatch, [resp(429), resp(200, OK)])
    llm = make(sleeps, min_interval=3.0)
    llm.chat([], [])
    assert llm.min_interval == 4.5


def test_gives_up_after_retries_and_marks_rate_limited(monkeypatch, sleeps):
    script(monkeypatch, [resp(429)] * 3)
    with pytest.raises(L.ProviderError) as e:
        make(sleeps, max_retries=2).chat([], [])
    assert e.value.rate_limited and "SECRETKEY" not in str(e.value)


def test_client_errors_are_not_retried_and_do_not_leak_the_key(monkeypatch, sleeps):
    script(monkeypatch, [resp(400, {"error": "bad SECRETKEY request"})])
    with pytest.raises(L.ProviderError) as e:
        make(sleeps).chat([], [])
    assert not sleeps and not e.value.rate_limited and "SECRETKEY" not in str(e.value)


def test_network_errors_are_retried(monkeypatch, sleeps):
    script(monkeypatch, [httpx.ConnectTimeout("t"), resp(200, OK)])
    assert make(sleeps).chat([], []).text == "hi"


def test_fallback_to_the_second_provider_is_recorded(monkeypatch, sleeps):
    script(monkeypatch, [resp(429)] * 2 + [resp(200, OK)])
    a, b = make(sleeps, max_retries=1), L.OpenAICompatLLM("https://y/v1", "K2", "m2", "groq", sleep=sleeps.append)
    chain = L.FallbackLLM([a, b])
    r = chain.chat([], [])
    assert r.provider == "groq" and chain.fallbacks == 1 and chain.answered == {"groq": 1}


def test_all_providers_failing_raises(monkeypatch, sleeps):
    script(monkeypatch, [resp(500)] * 4)
    chain = L.FallbackLLM([make(sleeps, max_retries=1), L.OpenAICompatLLM("https://y/v1", "K2", "m2", "groq", max_retries=1, sleep=sleeps.append)])
    with pytest.raises(L.ProviderError):
        chain.chat([], [])


def test_build_llm_chains_gemini_then_groq_and_can_disable_fallback():
    s = load_settings({"GEMINI_API_KEY": "g", "GEMINI_MODEL": "gm", "GROQ_API_KEY": "q", "GROQ_MODEL": "qm"})
    assert [p.name for p in L.build_llm(s).providers] == ["gemini", "groq"]
    assert [p.name for p in L.build_llm(s, fallback=False).providers] == ["gemini"]
    assert [p.name for p in L.build_llm(load_settings({"GROQ_API_KEY": "q", "GROQ_MODEL": "qm"}), fallback=False).providers] == ["groq"]  # Groq alone is still used
    with pytest.raises(L.LLMNotConfigured):
        L.build_llm(load_settings({}))


def test_pacing_waits_between_requests(monkeypatch, sleeps):
    script(monkeypatch, [resp(200, OK), resp(200, OK)])
    t = [0.0]
    llm = L.OpenAICompatLLM("https://x/v1", "k", "m", "p", min_interval=5.0, sleep=sleeps.append, clock=lambda: t[0])
    llm.chat([], [])
    t[0] = 1.0
    llm.chat([], [])
    assert sleeps == [4.0]


def test_provider_metadata_on_tool_calls_is_kept_and_echoed_back(monkeypatch, sleeps):
    """Gemini 3 needs the thought_signature of a tool call sent back with it, or the next turn is rejected with HTTP 400."""
    body = {"choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "extra_content": {"google": {"thought_signature": "SIG"}},
                                                                      "function": {"name": "gate_timeline", "arguments": "{\"gate\": \"G\"}"}}]}}]}
    script(monkeypatch, [resp(200, body)])
    r = make(sleeps).chat([], [])
    assert r.tool_calls[0].extra == {"google": {"thought_signature": "SIG"}}

    from lineage.agent.loop import run_agent

    class Tools:
        SCHEMAS = []
        def call(self, name, args):
            return {"tool": name, "params": args, "evidence": [], "path": [], "status": "insufficient_evidence"}

    fake = L.FakeLLM([L.LLMResponse(tool_calls=[L.ToolCall("c1", "gate_timeline", {"gate": "G"}, {"google": {"thought_signature": "SIG"}})]), L.LLMResponse("done")])
    import lineage.agent.loop as loop
    monkeypatch.setattr(loop.ToolBox, "SCHEMAS", [])
    run_agent(fake, Tools(), "q")
    assistant = next(m for m in fake.seen[1] if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "SIG"}}
