"""Fetch merged pull requests (authors, reviews, approvals, files touched) for three SIGs from the GitHub GraphQL API. Read-only.

Run: `uv run --env-file .env python -m lineage.ingest.github_prs [--sigs sig-storage,sig-node,sig-apps] [--since 2024-10-15] [--until 2026-10-15]`
     `uv run python -m lineage.ingest.github_prs --normalize`   (cache -> snapshot/prs.jsonl, no network)

Rules (CLAUDE.md §4): one token, <= 5,000 points/hour, <= 100 per page, requests over 10 s time out, cache every response and never refetch.
  * every response is written to data/raw/github_cache/ (git-ignored) keyed by the exact query, so a rerun or a resume costs nothing
  * the search API returns at most 1,000 results per query, so the period is cut into months and a month with >= 1,000 hits is split in two
  * the remaining budget is read from each response; below a safety margin we sleep until the window resets (no hammering, no secondary limits)
  * the token goes only into an Authorization header; it is never put in a URL or a log line
Scope: PRs with the label sig/storage, sig/node or sig/apps (the three SIGs of the plan; sig-storage is the demo case).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO

API = "https://api.github.com/graphql"
CACHE = Path("data/raw/github_cache")
SNAPSHOT = Path("snapshot/prs.jsonl")
REPO = "kubernetes/kubernetes"
PAGE = 25
FILES = 100  # per PR; more than that is flagged as truncated
REVIEWS = 30
SAFETY = 150  # keep this many points in reserve

QUERY = """
query($q: String!, $after: String, $n: Int!) {
  rateLimit { cost remaining resetAt }
  search(query: $q, type: ISSUE, first: $n, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      number title url mergedAt createdAt additions deletions changedFiles baseRefName
      author { login }
      mergedBy { login }
      labels(first: 20) { nodes { name } }
      files(first: %d) { totalCount nodes { path } }
      reviews(first: %d) { totalCount nodes { author { login } state submittedAt } }
    } }
  }
}""" % (FILES, REVIEWS)


class GithubError(RuntimeError):
    pass


class GithubGraphQL:
    def __init__(self, token: str, cache_dir: Path = CACHE, sleep=time.sleep, now=lambda: datetime.now(timezone.utc), timeout: float = 60.0):
        self.token, self.cache_dir, self._sleep, self._now, self.timeout = token, cache_dir, sleep, now, timeout
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.remaining: int | None = None
        self.reset_at: datetime | None = None
        self.stats = {"requests": 0, "cache_hits": 0, "points": 0, "waits": 0}

    def _path(self, variables: dict) -> Path:
        return self.cache_dir / (hashlib.sha1(json.dumps([QUERY, variables], sort_keys=True).encode()).hexdigest() + ".json")

    def _wait_for_budget(self) -> None:
        if self.remaining is not None and self.remaining < SAFETY and self.reset_at:
            secs = max(1.0, (self.reset_at - self._now()).total_seconds()) + 5
            log.info("rate limit nearly used (%d left); sleeping %.0f s until it resets", self.remaining, secs)
            self.stats["waits"] += 1
            self._sleep(secs)
            self.remaining = None

    def query(self, variables: dict) -> dict:
        path = self._path(variables)
        if path.exists():
            self.stats["cache_hits"] += 1
            return json.loads(path.read_text())
        for attempt in range(8):
            self._wait_for_budget()
            self.stats["requests"] += 1
            try:
                r = httpx.post(API, json={"query": QUERY, "variables": variables}, headers={"Authorization": f"Bearer {self.token}"}, timeout=self.timeout)
            except httpx.HTTPError as e:
                err, wait = f"network error {type(e).__name__}", 10.0 * (attempt + 1)
            else:
                if r.status_code == 200 and "errors" not in r.json():
                    data = r.json()["data"]
                    rl = data["rateLimit"]
                    self.remaining, self.reset_at = rl["remaining"], datetime.fromisoformat(rl["resetAt"].replace("Z", "+00:00"))
                    self.stats["points"] += rl["cost"]
                    path.write_text(json.dumps(data))
                    return data
                if r.status_code in (403, 429) or (r.status_code == 200 and "RATE_LIMITED" in r.text):
                    ra = r.headers.get("retry-after")
                    err, wait = "rate limited", float(ra) if ra and ra.isdigit() else 70.0 * (attempt + 1)
                    self.remaining = 0
                    self.reset_at = self._now() + timedelta(seconds=wait)
                elif r.status_code >= 500 or (r.status_code == 200 and any(x in r.text for x in ("timedout", "RESOURCE_LIMITS", "Something went wrong"))):
                    err, wait = f"server trouble (HTTP {r.status_code})", 15.0 * (attempt + 1)
                else:
                    raise GithubError(f"HTTP {r.status_code}: {r.text[:160].replace(self.token, '***')}")
            log.warning("github: %s; waiting %.0f s (attempt %d/8)", err, wait, attempt + 1)
            self._sleep(wait)
        raise GithubError("gave up after 8 attempts")


def months(since: date, until: date) -> list[tuple[date, date]]:
    out, cur = [], since
    while cur <= until:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((cur, min(nxt - timedelta(days=1), until)))
        cur = nxt
    return out


def search_string(sig: str, a: date, b: date) -> str:
    return f"repo:{REPO} is:pr is:merged label:sig/{sig.split('-', 1)[1]} merged:{a.isoformat()}..{b.isoformat()}"


def fetch_window(gh: GithubGraphQL, sig: str, a: date, b: date, pages: list | None = None) -> int:
    """All pages of one window; a window with 1,000 or more hits is split in two (the search API returns at most 1,000 results)."""
    q = search_string(sig, a, b)
    first = gh.query({"q": q, "after": None, "n": PAGE})
    total = first["search"]["issueCount"]
    if total >= 1000 and a < b:
        mid = a + (b - a) // 2
        return fetch_window(gh, sig, a, mid) + fetch_window(gh, sig, mid + timedelta(days=1), b)
    got, page, after = 0, first, None
    while True:
        got += len([n for n in page["search"]["nodes"] if n])
        info = page["search"]["pageInfo"]
        if not info["hasNextPage"]:
            return got
        after = info["endCursor"]
        page = gh.query({"q": q, "after": after, "n": PAGE})


def fetch_all(gh: GithubGraphQL, sigs: list[str], since: date, until: date) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sig in sigs:
        total = 0
        for a, b in months(since, until):
            n = fetch_window(gh, sig, a, b)
            total += n
            log.info("%s %s..%s: %d PRs (running total %d) | points used %d, left %s, cache hits %d", sig, a, b, n, total, gh.stats["points"], gh.remaining, gh.stats["cache_hits"])
        counts[sig] = total
    return counts


def normalize(cache_dir: Path = CACHE) -> list[dict]:
    """Cached responses -> one compact row per PR (deduplicated; a PR with two sig labels keeps both)."""
    prs: dict[int, dict] = {}
    for f in sorted(cache_dir.glob("*.json")):
        for n in json.loads(f.read_text())["search"]["nodes"]:
            if not n:
                continue
            labels = [x["name"] for x in n["labels"]["nodes"]]
            files = [x["path"] for x in n["files"]["nodes"]]
            row = {
                "number": n["number"], "title": n["title"], "url": n["url"], "merged_at": n["mergedAt"], "created_at": n["createdAt"], "base_ref": n.get("baseRefName"),
                "author": (n["author"] or {}).get("login", "").lower() or None, "merged_by": (n["mergedBy"] or {}).get("login", "").lower() or None,
                "sigs": sorted(x for x in labels if x.startswith("sig/")),
                "dirs": sorted({p.rsplit("/", 1)[0] if "/" in p else "" for p in files}),
                "files_total": n["files"]["totalCount"], "files_truncated": n["files"]["totalCount"] > len(files),
                "reviews": [{"login": (r["author"] or {}).get("login", "").lower(), "state": r["state"], "at": r["submittedAt"]} for r in n["reviews"]["nodes"] if r.get("author")],
                "reviews_total": n["reviews"]["totalCount"], "reviews_truncated": n["reviews"]["totalCount"] > len(n["reviews"]["nodes"]),
            }
            old = prs.get(row["number"])
            if old:
                row["sigs"] = sorted(set(old["sigs"]) | set(row["sigs"]))
                if old.get("base_ref") and not row["base_ref"]:  # an older cached response (before baseRefName was fetched) must not erase it
                    row["base_ref"] = old["base_ref"]
            prs[row["number"]] = row
    return sorted(prs.values(), key=lambda r: r["number"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sigs", default="sig-storage,sig-node,sig-apps")
    ap.add_argument("--since", default=(date.today() - timedelta(days=730)).isoformat())
    ap.add_argument("--until", default=date.today().isoformat())
    ap.add_argument("--normalize", action="store_true", help="only rebuild snapshot/prs.jsonl from the cache")
    args = ap.parse_args()
    if not args.normalize:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is not set (fine-grained, read-only, public repositories) in .env")
        gh = GithubGraphQL(token)
        counts = fetch_all(gh, args.sigs.split(","), date.fromisoformat(args.since), date.fromisoformat(args.until))
        log.info("fetched: %s | stats %s", counts, gh.stats)
    rows = normalize()
    SNAPSHOT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    log.info("prs: %d -> %s (%.1f MB); truncated files: %d, truncated reviews: %d", len(rows), SNAPSHOT, SNAPSHOT.stat().st_size / 1e6,
             sum(r["files_truncated"] for r in rows), sum(r["reviews_truncated"] for r in rows))


if __name__ == "__main__":
    main()
