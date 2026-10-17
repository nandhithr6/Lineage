# Deploying Lineage

Status legend: **[tested]** means it was run on a development machine; **[untested]** means it is written from documentation
and has not been run yet. Nothing here has been deployed publicly. The baseline is the complete local setup in the README (`docker compose up --build`).

## What runs where

| Piece | Where | Why |
|---|---|---|
| FalkorDB + loader + API | one small server, `docker compose` | the graph needs persistence and no memory cap; the free FalkorDB Cloud tier deletes data after days of inactivity |
| Frontend (`web/`) | Vercel (static) | it is only HTML/JS; it calls the API |

Measured on a development machine **[tested]**: the whole stack uses about 245 MiB of RAM (FalkorDB 187 MiB, API 57 MiB) with a
14 MB graph, image size 360 MB, snapshot 3.3 MB. A 1 GB server should be comfortable; 512 MB is probably enough but unverified.

## 0. Easiest: ONE container, one URL (recommended for a free host) **[tested locally]**

`Dockerfile.single` packs FalkorDB, the loader, the detector, the API **and the built web UI** into one image. Nothing needs to persist: on every start it loads the committed `snapshot/` into an in-container FalkorDB (about a minute) and serves
everything on one port (`$PORT`, default 7860): the UI at `/`, the API at `/api`, a platform health check at `/healthz`. The database listens on 127.0.0.1 inside the container only.

```bash
docker build -f Dockerfile.single -t lineage-single .          # about 3 minutes; the image is ~1.1 GB (mostly the FalkorDB base)
docker run -p 7860:7860 -e GEMINI_API_KEY -e GEMINI_MODEL -e GROQ_API_KEY -e GROQ_MODEL lineage-single
```

Verified on 2026-10-16: the container loads the snapshot, finds the 2 findings, serves the UI and API from one port, answers a real chat question, and its log contains no keys.

**To put it on a host** (not done): any host that runs a Docker image works (Render, Fly.io, Koyeb, Railway...). Point it at `Dockerfile.single` (or `Dockerfile.slim` for a small free plan, see below), expose port 7860 (or let it use `$PORT`), use `/healthz`
as the health check, and add the model keys as **secrets / environment variables, never in the repository**: `GEMINI_API_KEY`, `GEMINI_MODEL`, optionally `GROQ_API_KEY`, `GROQ_MODEL`. Host-specific clicks are **untested** here. Allow about 1 GB of RAM.

**Public-site safety:** a public chat endpoint would let strangers spend your free-tier quota, so `/api/ask` allows `ASK_PER_IP_PER_HOUR` (default 12) per visitor and `ASK_PER_DAY` (default 150) in total, then answers 429 with a clear message;
findings, Explore and the graph keep working. The free Gemini tier is small (about 20 requests a day on the flagship, more on the flash-lite model), so expect the chat to hit its limit on a busy day and fall back to Groq.

## 0b. Free web host without a card: Render with the slim image **[deployed by the owner on 2026-10-17; smoke test below passed on the live site]**

Live: https://lineage-faov.onrender.com/ . Checked from outside on 2026-10-17: `/healthz` 200, `/api/health` (`embeddings: false`), `/api/findings` (6 items), `/api/snapshots`, `/api/search` ("unavailable" as designed), a saved Ask answer, and the MCP endpoint answers an initialize request.

`Dockerfile.slim` is the same single container WITHOUT the local embedding model, for a host with little memory. **Measured here** (Docker, 512 MB memory limit, no OOM kill): image **749 MB**; RAM **214 MiB peak while starting, about 150 MiB idle and after
requests**; healthy after **13 s on 1 CPU and 44 s on 0.25 CPU**. In this build `/api/search` answers "unavailable" and `search_text` is not offered to the model (both say "available in the local setup"), the contrast toggle uses keyword (TF-IDF) retrieval and says so,
and the saved demo answers (including the saved vector-retrieval hero answer, labelled as such) still work. Everything else is identical.

What Render's free web service gives (from render.com/docs/free, read on 2026-10-17): no payment method is needed to deploy; **750 free instance hours a month**; the service **spins down after 15 minutes without traffic and takes "about one minute" to wake**; no persistent disk (the graph is rebuilt from the
snapshot on every start, so memory notes last until a restart); one instance only. That page does not state RAM or CPU for the free plan (I believe 512 MB and a fraction of a CPU: check the plan page), and it does not mention Docker services; this has not been run on Render.

Manual steps (the safer route; `render.yaml` is the same thing as a Blueprint):
1. render.com, sign up with GitHub (no card). **New +** -> **Web Service** -> **Build and deploy from a Git repository** -> pick `nandhithr6/Lineage`.
2. **Language:** Docker. **Dockerfile Path:** `./Dockerfile.slim`. **Instance Type:** Free. **Health Check Path:** `/healthz`. Leave the port alone: Render injects `PORT` and the container listens on it.
3. **Environment variables** (Advanced): `GEMINI_API_KEY` and `GROQ_API_KEY` (a key made for the demo), `GEMINI_MODEL` = `gemini-3.1-flash-lite`, `GROQ_MODEL` = the model in your `.env`, `LLM_MIN_INTERVAL` = `5`, `ASK_PER_IP_PER_HOUR` = `12`, `ASK_PER_DAY` = `150`. Without any key the site still works (saved answers, findings, trace, Explore).
4. **Create Web Service.** The first build takes a few minutes (it builds the UI and installs the dependencies). When it says Live, open the `onrender.com` URL: the first request after a sleep takes about a minute, and the page may look empty until the graph has loaded.
5. **Smoke test:** `/healthz` -> `{"status":"ok"}`; `/api/health` -> `"embeddings": false`; Findings shows 2 findings and 4 review items; Ask -> "Why is the Resource Quotas page stale?" answers instantly; the two refusal questions say Unknown entity; `/api/changes-since?since=latest` returns a summary;
   `npx @modelcontextprotocol/inspector --cli https://<service>.onrender.com/mcp/ --transport http --method tools/list` lists 11 tools.
6. If the build runs out of memory or minutes, or the free plan will not run a Docker service, stop: the complete local setup (README) and `scripts/share_demo.sh` are the fallback and need nothing from Render.

## 1. Server with compose (alternative: a VPS you control; the API only, with a persistent graph volume)

```bash
# on the server (any Linux with Docker + the compose plugin)
git clone <your public repo url> lineage && cd lineage
cp .env.example .env
# edit .env: set a long random FALKORDB_PASSWORD, and CORS_ORIGINS to your Vercel URL (comma-separated if several)
# optional: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL to enable the chat
docker compose -f docker-compose.stack.yml up -d --build        # [tested locally]
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
