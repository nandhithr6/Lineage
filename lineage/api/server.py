"""One-process server for single-container deployment: the API under /api and the built web UI at / (same origin, so no CORS and no second host).

The read-only MCP server (lineage/mcp_server.py) is served over streamable HTTP at /mcp/ on the same port.

Run: `uvicorn lineage.api.server:app --port 7860`   (the Docker image `Dockerfile.single` does this)
Without a built UI (web/dist missing) only /api and /healthz are served.
"""
from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from lineage.api.main import app as api
from lineage.api.main import warm_caches


def build(dist: Path | None = None, mcp: bool = True) -> FastAPI:
    mcp_server = None
    if mcp:
        from mcp.server.transport_security import TransportSecuritySettings

        from lineage.mcp_server import build_server

        mcp_server = build_server()
        # Public read-only data and no credentials: the localhost-only Host check would reject the hosting platform's domain, so it is switched off for this app.
        mcp_app = mcp_server.streamable_http_app(streamable_http_path="/", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Mounted sub-apps do not get their own startup events, so the ownership caches are warmed here (see api.main.warm_caches), and the MCP session manager is started.
        if os.environ.get("LINEAGE_NO_WARMUP") != "1":
            threading.Thread(target=warm_caches, daemon=True, name="warm-caches").start()
        if mcp_server is not None:
            async with mcp_server.session_manager.run():
                yield
        else:
            yield

    root = FastAPI(title="Lineage (single container)", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @root.get("/healthz", include_in_schema=False)
    def healthz() -> dict:  # for the hosting platform's health check; /api/health reports the database and LLM state
        return {"status": "ok"}

    root.mount("/api", api)
    if mcp_server is not None:
        root.mount("/mcp", mcp_app)
    dist = dist or Path(os.environ.get("WEB_DIST", "web/dist"))
    if (dist / "index.html").exists():
        root.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return root


app = build()
