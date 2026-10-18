# Demo video

## The main video: a motion-graphics explainer

The main demo video is a motion-graphics explainer built from the real product screenshots and diagrams (the production files are kept locally and are not part of this repository). It is about 3 minutes, every slide carries a caption so it works **with the sound off**, and it has a narration track (Gemini speech: natural, but still synthetic, so recording your own voice over the captions is the upgrade if you want one). Judges mostly decide from the video first, then the README, so it stands alone: the problem, the idea, the proof, the checks, FalkorDB, the honest numbers, and where to try it.

Before uploading: watch it once end to end and cut or reword anything that is not true to you; upload it (YouTube unlisted or public works, or a public Drive link; check it plays when logged out); then put the link in the README ("Demo video") and the blog.

---

## Live screen-recording plan (the alternative, or an addition)


Status: a plan only, to be recorded after the code is frozen. The older, more detailed click-by-click script is [demo_script.md](demo_script.md); this plan adds the pieces it lacks (FalkorDB Browser, MCP, the honest evaluation) and fixes the order.
Rule: **do not show a step you have not rehearsed on the live site the same day.** Cut anything that fails.

## Before you hit record (10 minutes)

1. Open <https://lineage-faov.onrender.com/> and wait until the **Home** page shows numbers. A free instance sleeps after 15 idle minutes and takes about a minute to wake, so wake it, then keep a tab open until you finish. (A 10-minute uptime ping on `/healthz` keeps it awake.)
2. Local, for the FalkorDB shot: `docker compose up --build`, then `http://localhost:3000` (the FalkorDB Browser). Have this query ready to paste:
   `MATCH (g:FeatureGate {name:'LocalStorageCapacityIsolation'})-[s:STAGE_IN]->(r:Release) RETURN g, s, r`
   (The Browser is **not** on the live site; it is a local developer tool, so record it from your machine.)
3. MCP shot: in a terminal run `npx -y @modelcontextprotocol/inspector`, open the address it prints (usually `http://localhost:6274`), choose **Streamable HTTP**, URL `https://lineage-faov.onrender.com/mcp/`, **Connect**. Rehearse it once: the exact Inspector screens were not re-checked in this session (the command-line form was).
   Terminal-only fallback (also fine on camera): `npx -y @modelcontextprotocol/inspector --cli https://lineage-faov.onrender.com/mcp/ --transport http --method tools/list`
4. Browser at 100% zoom, a clean window, notifications off, screen about 1080p. Record the voice-over live or after; short sentences.

## Which Ask questions are safe to show

You reviewed the saved Ask answers: **4 of 7 were judged not supported** (the hero answer among them), and they were regenerated afterwards. Until you have re-checked the new ones with `python3 scripts/verify.py answers`:
- Show the hero through the **Findings page** (it is built from graph rows, not from a model answer), not through the saved Ask answer.
- In Ask, use only questions whose answers you have confirmed. Before the judging round, the two that were judged supported were *the CronJob time-zones label* and *what design decision introduced LocalStorageCapacityIsolation*, plus the plain-retrieval hero answer used in the contrast.
- The two refusal examples (a typo'd gate name, release 1.45) need no model and are safe.

## Shot list

| Time | Show | Say (one idea per line) |
|---|---|---|
| 0:00 to 0:15 | **Home** page, slow scroll to the numbers | "Kubernetes is run like a 3,000-person company. Its docs carry hand-typed badges like *Alpha since v1.8* that go stale and nobody is told. Lineage is a company brain, built on FalkorDB, that notices, and proves every answer." |
| 0:15 to 1:00 | **Findings**, open **Resource Quotas**: side-by-side, the "ruled out" note, then scroll to *5 · Graph path* and press **Replay the trace** (it walks badge, gate, history, KEP, team, verdict, one step at a time), then the pinned source links | "The docs say alpha since 1.8. The verified history says stable since 1.25 and the gate is gone. Nobody told Lineage which feature this badge is about: it worked it out, and ruled out look-alike gates that did not exist yet in 1.8. Every line links to the exact commit." |
| 1:00 to 1:15 | **Draft the corrected label**, then the reviewers | "It drafts the one-line fix and suggests who should review it, each with a reason. It only drafts. It never posts to Kubernetes." |
| 1:15 to 1:40 | **Ask**: tick *Answer with plain retrieval too*, ask a confirmed question; point at the evidence chips and the trace table | "Same model, same text. On the right, plain retrieval: it cannot follow a relationship and nothing checks it. On the left, every claim has an evidence chip and the trace shows each query." |
| 1:40 to 1:55 | Click the typo'd-gate example, then the release-1.45 one | "If the graph does not know a name, it says so and runs no query. A validator throws away any answer that names something not in the evidence." |
| 1:55 to 2:10 | **Explore**: the whole-graph map (hover a circle), then `pkg/controller/job` as of 1.30 vs 1.37 | "This is the whole graph by type: ten thousand nodes and tens of thousands of relationships. Facts that change keep when they were true, so we can ask who owned this folder in 1.30." |
| 2:10 to 2:25 | **Local FalkorDB Browser**: paste the query, show the nodes and edges | "This is the graph underneath. Every page here is a query to FalkorDB. PageRank for central reviewers, shortest paths for how two things connect, a vector index for search: all inside the database. Take it away and nothing works." |
| 2:25 to 2:45 | **MCP**: Inspector connected to the live `/mcp/`, **List Tools**, then run `gate_timeline` with `LocalStorageCapacityIsolation` (or `owners_of` with `as_of` = `1.45` for the refusal) | "The same eleven read-only tools are an MCP server, so any AI client can ask for evidence and cite it. Ask about release 1.45 and it answers *insufficient evidence*, not a guess." |
| 2:45 to 3:00 | The README evaluation table | "We tested it on questions we wrote ourselves and ran once: Lineage got 8 of 15, plain retrieval 5 and 6. Lower than our own draft set, and we show both. We found two cases where it should have refused and did not." |

## Lines to avoid

- Do not say recall: we do not know how many stale labels exist (most badges name no gate and cannot be checked).
- Do not say the retrieval baseline is weak in general: it is the same model on the same text, without the graph or the checks.
- Do not say the held-out score is final: it was judged by a model and a person has not yet checked every verdict.
- Do not say "live" for anything that only runs locally (the FalkorDB Browser).

## MCP: what it is, in one breath (if asked)

MCP (Model Context Protocol) is a standard way for AI tools such as Claude to call outside tools. Lineage exposes its own eleven read-only graph tools that way (`/mcp/` on the live site, or `python -m lineage.mcp_server` for desktop clients). Each returns the same evidence bundle the web app uses, with commit-pinned links, so another AI can answer from Lineage and cite it. There are no write tools and no free-form queries.

## Fallbacks

- The live site is asleep or slow: record from the local `docker compose up` (same app, port 7860) and say so, or use `sh scripts/share_demo.sh` for a temporary public link.
- The Inspector window misbehaves: show the terminal `--cli` command; the `tools/list` result is the shot.
- A model quota error in Ask: use the saved example questions, which need no key.
- Cut order if over 3 minutes: the FalkorDB Browser shot, then the unknown-entity pair, then the draft-fix reviewers.

## After recording

Upload the video, then replace the placeholder in the README ("Demo video: *(link added at submission)*") with the link. Re-read the README's evaluation table once so the video and the page say the same numbers.
