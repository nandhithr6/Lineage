"""FalkorDB connection helpers and a connectivity check: `uv run --env-file .env python -m lineage.graph.client`."""
from __future__ import annotations

import logging

from falkordb import FalkorDB, Graph

from lineage.config import Settings, load_settings

log = logging.getLogger(__name__)


def connect(settings: Settings | None = None) -> FalkorDB:
    s = settings or load_settings()
    return FalkorDB(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password)


def get_graph(settings: Settings | None = None) -> Graph:
    s = settings or load_settings()
    return connect(s).select_graph(s.falkordb_graph)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = connect()
    # ro_query on a graph that does not exist yet raises "Invalid graph operation on empty key",
    # so check the server itself rather than a specific graph.
    log.info("FalkorDB reachable: ping=%s, graphs=%s", db.connection.ping(), db.list_graphs())
