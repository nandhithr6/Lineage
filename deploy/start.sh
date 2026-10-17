#!/bin/sh
# Single-container start: FalkorDB (localhost only) -> load the committed snapshot -> detector -> API + UI on $PORT.
# Nothing needs to persist: the 7 MB graph is rebuilt from snapshot/ on every start (about a minute).
set -eu
export FALKORDB_HOST=127.0.0.1 FALKORDB_PORT=6379
# the database is reachable only from inside the container; the password is random per start unless provided
FALKORDB_PASSWORD="${FALKORDB_PASSWORD:-$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')}"
export FALKORDB_PASSWORD REDISCLI_AUTH="$FALKORDB_PASSWORD"
mkdir -p /tmp/falkordb
redis-server --loadmodule "${FALKORDB_BIN_PATH:-/var/lib/falkordb/bin}/falkordb.so" --bind 127.0.0.1 --port "$FALKORDB_PORT" --requirepass "$FALKORDB_PASSWORD" \
  --dir /tmp/falkordb --save "" --appendonly no --protected-mode yes > /tmp/falkordb/redis.log 2>&1 &
i=0
until redis-cli -p "$FALKORDB_PORT" ping 2>/dev/null | grep -q PONG; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "FalkorDB did not start:"; tail -20 /tmp/falkordb/redis.log; exit 1; }
  sleep 1
done
echo "FalkorDB is up; loading the snapshot..."
python -m lineage.graph.loader
python -m lineage.detect.drift --write | sed -n '/== Counts/,$p'
echo "Serving on port ${PORT:-7860}"
exec uvicorn lineage.api.server:app --host 0.0.0.0 --port "${PORT:-7860}" --log-level info
