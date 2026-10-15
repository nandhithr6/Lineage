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


def load_settings(env: dict[str, str] | None = None) -> Settings:
    e = os.environ if env is None else env
    return Settings(
        falkordb_host=e.get("FALKORDB_HOST", "localhost"),
        falkordb_port=int(e.get("FALKORDB_PORT", "6379")),
        falkordb_password=e.get("FALKORDB_PASSWORD") or None,
        falkordb_graph=e.get("FALKORDB_GRAPH", "lineage"),
    )
