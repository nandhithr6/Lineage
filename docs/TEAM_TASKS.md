# Tasks for Abiram (and anyone else helping): UI, README, Render, video support

**Time limits** (IST): feature freeze **Oct 18, 2 PM**; demo video **by 6 PM**; submission **by 9 PM**. Anything below that is not done by the freeze does not go in. Work in small pieces and commit after each.
**Safe areas:** `web/` (UI), `docs/`, `README.md`. **Ask before touching:** `lineage/` (Python), `snapshot/`, `eval/` (the held-out files are frozen), `Dockerfile*`, `render.yaml`.
**Before every commit:** `cd web && npm test && npm run build`, and `uv run --env-file .env pytest -q` if you touched anything outside `web/`. Work on a branch (`git checkout -b abiram/ui`), open a pull request into `main`, let the owner merge: nobody force-pushes.
**Run it:** `docker compose up --build` then <http://localhost:7860>; for fast UI work: `cd web && npm install && npm run dev` (needs the API: `docker compose up`, the dev server proxies `/api`). Live site: <https://lineage-faov.onrender.com/>.

## A. UI plan (in order of value per hour; each item says what "done" means)

| # | Task | Time | Done when |
|---|---|---|---|
| U1 | **Phone and narrow-screen pass.** Check every page at 375 px and 768 px (browser devtools). Fix overflow: nav wrapping, the Ask page's two columns and trace table, the Explore graph height, the finding page's side-by-side columns. `web/src/styles.css`. | 1.5 h | No horizontal scroll on any page at 375 px; every button reachable; screenshots before/after in the PR. |
| U2 | **First-visit explainer on Findings.** A short banner ("Lineage compares the 'Beta since v1.x' badges on Kubernetes docs with each feature's verified history"), the 4 numbers (findings, review items, labels checked, confident links), and a small glossary popover for SIG / KEP / feature gate / OWNERS (text is in README section "Glossary" of CLAUDE.md). `FindingsPage.jsx`. | 1.5 h | A stranger can say what the page is for in 10 seconds (ask someone). |
| U3 | **Keep the example questions visible in Ask.** Today the chips vanish after the first message; keep them as a "Try another" row above the input. Include the two refusal examples and label which ones need no key. `ChatPage.jsx`. | 45 min | Chips stay; clicking one while another is loading does nothing. |
| U4 | **Insights page visuals** with plain CSS bars (no new dependency): quiet share per folder as a bar, PageRank as a bar, a one-line "how to read this" under each table. `InsightsPage.jsx`. | 1.5 h | Each table has a bar column; text explains orphaned vs dormant in one sentence. |
| U5 | **What-changed page**: group gate stage changes by transition ("beta to stable" first), collapse lists over 10, add a "copy as markdown" button for the summary. `ChangesPage.jsx`. | 1.5 h | Long lists collapse; the copied markdown pastes cleanly into a README. |
| U6 | **Graph view polish** (`GraphView.jsx`): legend items as toggles that hide a node type; clicking a node shows its properties and source link in a panel under the graph; zoom in/out buttons. | 2-3 h | Toggling and the panel work on Explore and on a finding's graph. Risky: keep it behind a branch, merge only if it works by noon Oct 18. |
| U7 | **Accessibility pass**: visible focus rings, `aria-label` on icon-only controls, colour contrast (dark and light) with the browser's Lighthouse or axe extension; fix what it flags. | 1 h | Lighthouse accessibility score of 90 or more on Findings and Ask. |
| U8 | **Link previews and tab polish**: `web/index.html` title, description, a favicon, Open Graph tags (so a pasted link looks good in chat). | 30 min | Pasting the live URL into a chat shows a title and description. |
| U9 | **Entity search box in Explore** (type "job controller" or a gate name). Needs a tiny read-only API (`GET /api/names?kind=gate&q=...`); ask Claude for the endpoint, then build the dropdown. | 1.5 h | Typing 3 letters suggests real entities; picking one shows its graph. |
| U10 | **Empty and error states**: every page shows something useful when the API is slow (skeleton), empty (a sentence), or down (the "waking up" banner exists already). | 1 h | Stop the API and reload each page: no blank screens. |

Do not add heavy dependencies (charting or UI frameworks): the bundle is small on purpose and the free host is slow.

## B. Render (the live demo) improvements

| # | Task | Notes |
|---|---|---|
| R1 | **Keep it awake on the judging days.** A free instance sleeps after 15 minutes idle (about a minute to wake). A free uptime pinger (UptimeRobot or cron-job.org) hitting `https://lineage-faov.onrender.com/healthz` every 10 minutes keeps it awake. A whole month is about 744 hours and the free allowance is 750, so one always-on service fits, but only if nothing else on the account uses hours: switch the pinger on **Oct 18** and off after the results. |
| R2 | **Measure and write down the cold start.** Let it sleep, then `curl -s -o /dev/null -w "%{time_total}\n" https://lineage-faov.onrender.com/api/health` and note the time; put the number in `docs/deploy.md` (currently "about a minute", unmeasured on Render). |
| R3 | **Freeze deploys.** After Oct 18 2 PM turn Auto-Deploy off in the Render dashboard (Settings, Build and Deploy) so a late push cannot break the site; deploy manually only for a fix. |
| R4 | **Smoke-test after every deploy** with the checklist in `docs/deploy.md` (health, findings, a saved Ask answer, the two refusals, MCP initialize). |
| R5 | **Watch the quota.** The chat key is on a free tier. If Ask starts answering 503/429, the saved questions still work; check `ASK_PER_DAY` and the Gemini usage page, and rotate the demo key after the judging period. Never put a key in the repo. |
| R6 | **Insights is now instant** (precomputed from the snapshot: it took 91 s on Render before). If the snapshot data ever changes, re-run `uv run --env-file .env python -m lineage.graph.ownership_views` and commit `snapshot/ownership_views.json` (a test fails if it is stale). |

## C. README and writing

| # | Task | Time |
|---|---|---|
| W1 | **"What the team did" section** (README, a placeholder today): the owner and Abiram each write what they decided, built, checked and learned, in their own words. The rules require meaningful human contribution; this is where it is stated. | 45 min (each) |
| W2 | **Shorten the README top.** One screen: the pitch, the live link, the 60-second run command, three screenshots from `docs/screenshots/` (hero trace, contrast view, memory). Move deep detail below or into `docs/`. Keep every required section (setup, graph model, queries, data sources and licenses, AI disclosure, limitations). | 1.5 h |
| W3 | **Architecture picture**: a clean diagram image (the mermaid in the README exported, or drawn) for the README and the blog. | 45 min |
| W4 | **AI-use disclosure review.** Read the README's disclosure section and make sure it says plainly what the AI did, what you checked, and what is still a draft (link `docs/VERIFY.md` results when done). | 30 min |
| W5 | **Blog post draft** (about 800 words): the problem (knowledge that quietly goes stale), the hero case (Resource Quotas: alpha in the docs, stable since 1.25), what the graph adds over retrieval (the contrast view), what we do when we do not know (unknown entity, not in graph), honest numbers with caveats, what is next. Outline in the owner's notes; draft in `docs/blog_draft.md`. | 2 h |
| W6 | **Proofread the UI text and README** once everything else is frozen (typos, consistent names: "feature gate", "design decision (KEP)"). | 30 min |

## D. Demo support
- **Rehearse `docs/demo_script.md` on the live site** (not localhost) and list anything that fails or is slow. The owner records the video at the end; the script is 8 steps and about 3 minutes.
- **Bug-bash the live site** on a phone and in a second browser on Oct 18 morning; write findings as GitHub issues or a list in `docs/bugs.md`.
- **Eval questions:** the owner supplies the independent held-out set; Abiram can also do `python3 scripts/verify.py gold` (see `docs/VERIFY.md`).

## Suggested split of the remaining time
- **Tonight (Oct 17):** Abiram: U1, U2, U3, R1 plan, W1 draft. Owner: `verify.py semantic`, `findings`, `answers`, then `links --sample 25`.
- **Oct 18 morning:** Abiram: U4/U5/U8 (whichever are done), W2, W3, R2; both: bug-bash, W6. Owner: held-out run, final README numbers.
- **Oct 18, noon:** stop adding features; merge or drop open branches. **2 PM:** freeze (turn off auto-deploy). **by 6 PM:** video. **by 9 PM:** submit.
