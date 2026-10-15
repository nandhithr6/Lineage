"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    falkordb_host: str
    falkordb_port: int
    falkordb_password: str | None
    falkordb_graph: str
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    gemini_api_key: str | None
    gemini_model: str | None
    gemini_embed_model: str | None
    groq_api_key: str | None
    groq_model: str | None
    llm_min_interval: float  # seconds between LLM requests (free-tier pacing)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    e = os.environ if env is None else env
    return Settings(
        falkordb_host=e.get("FALKORDB_HOST", "localhost"),
        falkordb_port=int(e.get("FALKORDB_PORT", "6379")),
        falkordb_password=e.get("FALKORDB_PASSWORD") or None,
        falkordb_graph=e.get("FALKORDB_GRAPH", "lineage"),
        llm_base_url=e.get("LLM_BASE_URL") or None,
        llm_api_key=e.get("LLM_API_KEY") or None,
        llm_model=e.get("LLM_MODEL") or None,
        gemini_api_key=e.get("GEMINI_API_KEY") or None,
        gemini_model=e.get("GEMINI_MODEL") or None,
        gemini_embed_model=e.get("GEMINI_EMBED_MODEL") or None,
        groq_api_key=e.get("GROQ_API_KEY") or None,
        groq_model=e.get("GROQ_MODEL") or None,
        llm_min_interval=float(e.get("LLM_MIN_INTERVAL", "5")),
    )


def llm_configured(s: Settings) -> bool:
    """True when at least one chat provider (Gemini, Groq or a generic OpenAI-compatible endpoint) is fully configured."""
    return bool((s.gemini_api_key and s.gemini_model) or (s.groq_api_key and s.groq_model) or (s.llm_base_url and s.llm_api_key and s.llm_model))
