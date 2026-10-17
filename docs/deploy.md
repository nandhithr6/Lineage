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

## 0. Easiest: ONE container, one URL (recommended for a free host) **[tested locally]**

`Dockerfile.single` packs FalkorDB, the loader, the detector, the API **and the built web UI** into one image. Nothing needs to persist: on every start it loads the committed `snapshot/` into an in-container FalkorDB (about a minute) and serves
everything on one port (`$PORT`, default 7860): the UI at `/`, the API at `/api`, a platform health check at `/healthz`. The database listens on 127.0.0.1 inside the container only.

```bash
docker build -f Dockerfile.single -t lineage-single .          # about 3 minutes; the image is ~1.1 GB (mostly the FalkorDB base)
docker run -p 7860:7860 -e GEMINI_API_KEY -e GEMINI_MODEL -e GROQ_API_KEY -e GROQ_MODEL lineage-single
```

Verified on 2026-10-16: the container loads the snapshot, finds the 2 findings, serves the UI and API from one port, answers a real chat question, and its log contains no keys.

**To put it on a host** (not done: it needs your account): any host that runs a Docker image works (Hugging Face Spaces with the Docker SDK, Render, Fly.io, Koyeb, Railway...). Point it at `Dockerfile.single`, expose port 7860 (or let it use `$PORT`), use `/healthz`
as the health check, and add the model keys as **secrets / environment variables, never in the repository**: `GEMINI_API_KEY`, `GEMINI_MODEL`, optionally `GROQ_API_KEY`, `GROQ_MODEL`. Host-specific clicks are **untested** here. Allow about 1 GB of RAM.

**Public-site safety:** a public chat endpoint would let strangers spend your free-tier quota, so `/api/ask` allows `ASK_PER_IP_PER_HOUR` (default 12) per visitor and `ASK_PER_DAY` (default 150) in total, then answers 429 with a clear message;
findings, Explore and the graph keep working. The free Gemini tier is small (about 20 requests a day on the flagship, more on the flash-lite model), so expect the chat to hit its limit on a busy day and fall back to Groq.

## 1. Server with compose (alternative: a VPS you control)

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

## Hugging Face Spaces (Docker SDK, free CPU): exact steps

Tested locally: the assembled folder builds and boots as user 1000 (what Spaces uses) with no keys, and every endpoint answers. Cold start to healthy took about 10 seconds here; allow 1-2 minutes on the free CPU, plus the first image build (about 5 minutes; the image is about 2 GB because it carries the local embedding model, 4 minutes to build here).

1. **Make the folder.** In the repo run `sh deploy/make_space.sh`. It writes `space_build/` (about 24 MB; `snapshot/prs.jsonl` is 10 MB and `snapshot/chunks.jsonl` 8 MB): a root `Dockerfile` (= `Dockerfile.single`), the README with the Spaces YAML header (`sdk: docker`, `app_port: 7860`), the code, `snapshot/` and the web sources. No secrets, no raw data.
2. **Create the Space.** huggingface.co, avatar menu, **New Space**. Name it (for example `lineage`), choose **Docker** as the SDK and **Blank** as the template, hardware **CPU basic (free)**, visibility **Public**, then **Create Space**.
3. **Add secrets** (Space page, **Settings**, **Variables and secrets**). New **secrets**: `GEMINI_API_KEY`, `GROQ_API_KEY`. New **variables**: `GEMINI_MODEL` = `gemini-3.1-flash-lite`, `GROQ_MODEL` = the model name in your `.env`, `LLM_MIN_INTERVAL` = `5`, `ASK_PER_IP_PER_HOUR` = `12`, `ASK_PER_DAY` = `150`. (Use a key you created for the demo, not your main one.)
4. **Upload the files.** Hugging Face rejects ordinary git pushes of files over 10 MB, and `snapshot/prs.jsonl` is just over: upload through the web page (it handles large files) or `huggingface-cli upload <you>/<space> space_build . --repo-type space`; with plain git you would first need `git lfs track "*.jsonl"`. Easiest: on the Space page choose **Files**, **Add file**, **Upload files**, drag in the *contents* of `space_build/` (not the folder itself), and commit. Alternative with git: `git clone https://huggingface.co/spaces/<you>/<space>`, copy the contents in, commit and push (use an access token with write permission as the password).
5. **Wait for the build.** The **App** tab shows the build log, then "Running". Open the page: Findings should list 2 findings and 4 review items; the footer of the About page shows the snapshot date and commit SHAs.
   Command-line route (needs a **write** access token from huggingface.co/settings/tokens): `pip install -U huggingface_hub`, `huggingface-cli login`, then `huggingface-cli upload <you>/<space> space_build . --repo-type space`.
6. **Smoke test** (replace the host with your Space's direct URL, `https://<you>-<space>.hf.space`):
   - `/api/health` returns `"status":"ok"` and `"llm_configured":true` (false is fine too: the saved answers still work).
   - Findings page: 2 findings and 4 review items; open **Resource Quotas**: "The docs say" next to "Verified history", and the "Trusted without a hand check" note.
   - Ask page: click "Why is the Resource Quotas page stale?" (a saved answer, instant), tick **Answer with plain retrieval too** and click it again (two columns); click the two refusal questions (Unknown entity, no key needed).
   - `/api/search?q=Which design document introduced LocalStorageCapacityIsolation` returns `"status":"ok"`; `/api/changes-since?since=latest` returns a summary; **What changed** and **Insights** pages load (Insights is slow for about 30 s after a cold start while the ownership scan warms).
   - MCP: `npx @modelcontextprotocol/inspector --cli https://<you>-<space>.hf.space/mcp/ --transport http --method tools/list` lists 11 tools.
7. **Keep it alive.** Free Spaces sleep after a period of inactivity and the graph rebuilds from the snapshot on wake (about a minute); open the page shortly before judging windows. Do not store anything you need on the Space: it has no persistent disk by design.
