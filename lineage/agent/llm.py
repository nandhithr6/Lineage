"""LLM wrapper: one method, `chat(messages, tools) -> LLMResponse`. Provider details stay in this file.

Providers (all OpenAI-style chat completions with tool calling, called with httpx; no provider SDK):
  * Gemini  - primary for the agent (also used for embeddings, see embeddings.py)
  * Groq    - fallback for chat only (it has no embeddings)
  * any generic OpenAI-compatible endpoint (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL), used only if the two above are not configured
Behaviour: pace requests (free tiers), retry 429/5xx with backoff (honouring Retry-After), then fall back to the next provider, and log which
provider answered each request. Model names come from .env (chosen by scripts/pick_models.py), never from this file.
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from lineage.config import Settings, load_settings

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO; keys must never reach a log

GEMINI_OPENAI = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ_OPENAI = "https://api.groq.com/openai/v1"
RETRYABLE = {429, 500, 502, 503, 504}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    extra: dict | None = None  # provider metadata that must be echoed back (Gemini 3: extra_content.google.thought_signature)


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str | None = None  # which provider answered (for logs and the eval report)


class LLM(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


class LLMNotConfigured(RuntimeError):
    pass


class ProviderError(RuntimeError):
    """A provider could not answer (after retries). `rate_limited` is True when the cause was HTTP 429."""

    def __init__(self, message: str, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, name: str = "openai-compat", timeout: float = 90.0,
                 max_retries: int = 4, min_interval: float = 0.0, sleep=time.sleep, clock=time.monotonic):
        self.base_url, self.api_key, self.model, self.name = base_url.rstrip("/"), api_key, model, name
        self.timeout, self.max_retries, self.min_interval = timeout, max_retries, min_interval
        self._sleep, self._clock, self._last = sleep, clock, None
        self.stats = {"requests": 0, "retries": 0, "rate_limited": 0, "errors": 0}

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> "OpenAICompatLLM":
        """Generic endpoint from LLM_BASE_URL/LLM_API_KEY/LLM_MODEL (kept for any other OpenAI-compatible provider)."""
        s = s or load_settings()
        if not (s.llm_base_url and s.llm_api_key and s.llm_model):
            raise LLMNotConfigured("Set GEMINI_API_KEY + GEMINI_MODEL (or GROQ_API_KEY + GROQ_MODEL) in .env to enable the chat agent")
        return cls(s.llm_base_url, s.llm_api_key, s.llm_model, "generic", min_interval=s.llm_min_interval)

    def _pace(self) -> None:
        if self.min_interval and self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        body: dict = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:  # some providers reject an empty tools list (plain question answering has none)
            body["tools"] = [{"type": "function", "function": t} for t in tools]
            body["tool_choice"] = "auto"
        last = "no attempt"
        rate_limited = False
        for attempt in range(self.max_retries + 1):
            self._pace()
            self.stats["requests"] += 1
            try:
                r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout, headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as e:
                last, status = f"network error {type(e).__name__}", 0
            else:
                status = r.status_code
                if status == 200:
                    return self._parse(r.json())
                last = f"HTTP {status}: {r.text[:160].replace(self.api_key, '***')}"
                if status not in RETRYABLE:
                    self.stats["errors"] += 1
                    raise ProviderError(f"{self.name} {last}")
            rate_limited = rate_limited or status == 429
            self.stats["rate_limited"] += status == 429
            if attempt == self.max_retries:
                break
            retry_after = r.headers.get("retry-after") if status else None
            delay = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else min(60.0, 2 ** (attempt + 1) * 2) + random.random()
            delay = min(delay, 90.0)
            self.stats["retries"] += 1
            log.warning("%s (%s): %s; retry %d/%d in %.0fs", self.name, self.model, last[:80], attempt + 1, self.max_retries, delay)
            self._sleep(delay)
            if status == 429:  # the free tier told us we are too fast: slow down for the rest of the run
                self.min_interval = min(max(self.min_interval * 1.5, 2.0), 60.0)
        self.stats["errors"] += 1
        raise ProviderError(f"{self.name} gave up after {self.max_retries + 1} attempts: {last}", rate_limited)

    def _parse(self, data: dict) -> LLMResponse:
        msg = data["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(c.get("id") or f"call_{len(calls)}", c["function"]["name"], args, c.get("extra_content")))
        log.info("provider=%s model=%s tool_calls=%d", self.name, self.model, len(calls))
        return LLMResponse(msg.get("content"), calls, self.name)


class FallbackLLM:
    """Try providers in order. On a provider failure (after its own retries) log it and move to the next; remember who answered."""

    def __init__(self, providers: list[OpenAICompatLLM]):
        if not providers:
            raise LLMNotConfigured("no chat provider configured")
        self.providers = providers
        self.answered: dict[str, int] = {}
        self.fallbacks = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        errors = []
        for i, p in enumerate(self.providers):
            try:
                resp = p.chat(messages, tools)
            except ProviderError as e:
                errors.append(str(e))
                if i + 1 < len(self.providers):
                    self.fallbacks += 1
                    log.warning("falling back from %s to %s: %s", p.name, self.providers[i + 1].name, str(e)[:100])
                continue
            self.answered[p.name] = self.answered.get(p.name, 0) + 1
            return resp
        raise ProviderError("all providers failed: " + " | ".join(errors))


def build_llm(s: Settings | None = None, fallback: bool = True) -> LLM:
    """Gemini first, Groq as fallback for chat (set fallback=False for a single-provider run, e.g. the fair Q&A comparison)."""
    s = s or load_settings()
    chain: list[OpenAICompatLLM] = []
    if s.gemini_api_key and s.gemini_model:
        chain.append(OpenAICompatLLM(GEMINI_OPENAI, s.gemini_api_key, s.gemini_model, "gemini", min_interval=s.llm_min_interval))
    if s.groq_api_key and s.groq_model and (fallback or not chain):
        chain.append(OpenAICompatLLM(GROQ_OPENAI, s.groq_api_key, s.groq_model, "groq", min_interval=min(s.llm_min_interval, 3.0)))
    if not chain:
        chain.append(OpenAICompatLLM.from_settings(s))
    return FallbackLLM(chain)


class FakeLLM:
    """Replays scripted responses in order; records what it was sent. For tests and offline development."""

    def __init__(self, script: list[LLMResponse]):
        self.script, self.seen = list(script), []

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.seen.append(json.loads(json.dumps(messages)))
        if not self.script:
            return LLMResponse("(script exhausted)")
        return self.script.pop(0)
