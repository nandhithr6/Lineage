# Deploying Lineage

Status legend: **[tested]** means it was run on a development machine; **[untested]** means it is written from documentation
and has not been run yet. Nothing here has been deployed publicly.

## What runs where

| Piece | Where | Why |
|---|---|---|
| FalkorDB + loader + API | one small server, `docker compose` | the graph needs persistence and no memory cap; the free FalkorDB Cloud tier deletes data after days of inactivity |
| Frontend (`web/`) | Vercel (static) | it is only HTML/JS; it calls the API |

Measured on a development machine **[tested]**: the whole stack uses about 245 MiB of RAM (FalkorDB 187 MiB, API 57 MiB) with a
14 MB graph, image size 360 MB, snapshot 3.3 MB. A 1 GB server should be comfortable; 512 MB is probably enough but unverified.

## 1. Server (one command after setup)

```bash
# on the server (any Linux with Docker + the compose plugin)
git clone <your public repo url> lineage && cd lineage
cp .env.example .env
# edit .env: set a long random FALKORDB_PASSWORD, and CORS_ORIGINS to your Vercel URL (comma-separated if several)
# optional: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL to enable the chat
docker compose up -d --build        # [tested locally]
```

What happens: FalkorDB starts and becomes healthy, a one-shot `loader` container loads `snapshot/*.jsonl` into the graph and
runs the drift detector, then the API starts on `127.0.0.1:8000`. The loader is idempotent, so running the command again is safe.

- **Crash recovery:** `docker compose up -d` again. If the volume was lost, the same command rebuilds the graph from the snapshot.
- **Never run `docker compose down -v` in production:** `-v` deletes the graph volume (it can be rebuilt, but the site would be empty meanwhile).
- **Ports:** FalkorDB (6379, 3000) and the API (8000) are bound to `127.0.0.1` on purpose. Do not publish 6379 or 3000.
- **Check:** `curl localhost:8000/health` should say `"database": true`.

## 2. HTTPS in front of the API **[untested]**

The browser frontend on Vercel is served over HTTPS, so the API must be too (browsers block mixed content). A reverse proxy
such as Caddy gets and renews certificates automatically. Example `Caddyfile` (replace the domain, point its DNS A record at the server):

```
api.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Run Caddy on the host (`apt install caddy` or the official image with `network_mode: host`). Open only ports 80 and 443 in the firewall.

## 3. Frontend on Vercel **[untested]**

1. Import the repository in Vercel. Set **Root Directory** to `web`. Framework preset: Vite. Build command `npm run build`, output `dist`.
2. Add the environment variable `VITE_API_URL` = your API's public URL (see `web/.env.example`).
3. Put the resulting Vercel URL into the server's `CORS_ORIGINS` and run `docker compose up -d` again to apply it.
4. Routing uses the URL hash (`#/findings/...`), so no rewrite rules are needed.

## 4. Smoke test after deploying

```bash
curl https://api.example.org/health
curl "https://api.example.org/findings?severity=finding"
```

Then open the Vercel URL: the Findings list should show the stale labels, and a finding's page should draw its graph.
Draft fixes work on the server without any website clone (they use the section text stored in the graph) **[tested in the container]**.

## 5. Keep it running after the hackathon

Judges may open the link days later. Use `restart: unless-stopped` (already set), enable automatic security updates on the server,
and check `docker compose ps` once after submission. The snapshot is a point-in-time copy (see `/about-data` for the date and commits).

## Costs

The software is free. The only possible cost is the server. Check the hackathon organisers about hosting credits before paying anyone.
