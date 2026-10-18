import json
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from lineage.ingest import github_prs as G


def pr(n, files=("pkg/volume/a.go", "pkg/volume/b.go", "README.md"), reviews=(("alice", "APPROVED"),), sigs=("sig/storage",), total_files=None, author="Bob"):
    return {"number": n, "title": f"PR {n}", "url": f"https://github.com/kubernetes/kubernetes/pull/{n}", "mergedAt": "2025-01-02T00:00:00Z", "createdAt": "2025-01-01T00:00:00Z",
            "additions": 1, "deletions": 1, "changedFiles": len(files), "baseRefName": "master", "author": {"login": author}, "mergedBy": {"login": "Merger"},
            "labels": {"nodes": [{"name": s} for s in sigs]}, "files": {"totalCount": total_files or len(files), "nodes": [{"path": p} for p in files]},
            "reviews": {"totalCount": len(reviews), "nodes": [{"author": {"login": r}, "state": s, "submittedAt": "2025-01-02T00:00:00Z"} for r, s in reviews]}}


def page(nodes, total, nxt=None, remaining=4000):
    return {"data": {"rateLimit": {"cost": 3, "remaining": remaining, "resetAt": "2030-01-01T00:00:00Z"},
                     "search": {"issueCount": total, "pageInfo": {"hasNextPage": nxt is not None, "endCursor": nxt}, "nodes": nodes}}}


class FakeGitHub:
    """Answers search queries from a script keyed by (query string, cursor); records every call."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, url, json=None, headers=None, timeout=None):
        v = json["variables"]
        self.calls.append((url, v, headers))
        key = (v["q"], v["after"])
        out = self.table.get(key) or self.table.get(("*", v["after"]))
        if isinstance(out, int):
            return httpx.Response(out, text="x", request=httpx.Request("POST", url))
        return httpx.Response(200, json=out, request=httpx.Request("POST", url))


def client(tmp_path, monkeypatch, table, sleeps=None):
    fake = FakeGitHub(table)
    monkeypatch.setattr(G.httpx, "post", fake)
    sleeps = sleeps if sleeps is not None else []
    return G.GithubGraphQL("SECRETTOKEN", tmp_path, sleep=sleeps.append, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)), fake, sleeps


def test_months_cover_the_period_without_gaps():
    ms = G.months(date(2024, 10, 15), date(2025, 1, 10))
    assert ms[0] == (date(2024, 10, 15), date(2024, 10, 31)) and ms[-1] == (date(2025, 1, 1), date(2025, 1, 10))
    assert [b for a, b in ms][:-1] == [date(2024, 10, 31), date(2024, 11, 30), date(2024, 12, 31)]
    assert all((ms[i + 1][0] - ms[i][1]).days == 1 for i in range(len(ms) - 1))


def test_pagination_and_cache_means_no_second_request(tmp_path, monkeypatch):
    q = G.search_string("sig-storage", date(2025, 1, 1), date(2025, 1, 31))
    gh, fake, _ = client(tmp_path, monkeypatch, {(q, None): page([pr(1), pr(2)], 3, nxt="c1"), (q, "c1"): page([pr(3)], 3)})
    assert G.fetch_window(gh, "sig-storage", date(2025, 1, 1), date(2025, 1, 31)) == 3 and len(fake.calls) == 2
    gh2, fake2, _ = client(tmp_path, monkeypatch, {})  # fresh client, same cache dir, empty server: must not call the network
    assert G.fetch_window(gh2, "sig-storage", date(2025, 1, 1), date(2025, 1, 31)) == 3 and fake2.calls == [] and gh2.stats["cache_hits"] == 2


def test_a_window_with_1000_results_is_split_in_two(tmp_path, monkeypatch):
    a, b = date(2025, 1, 1), date(2025, 1, 31)
    mid = a + (b - a) // 2
    t = {(G.search_string("sig-node", a, b), None): page([pr(1)], 1000),
         (G.search_string("sig-node", a, mid), None): page([pr(1), pr(2)], 2), (G.search_string("sig-node", mid + timedelta(days=1), b), None): page([pr(3)], 1)}
    gh, fake, _ = client(tmp_path, monkeypatch, t)
    assert G.fetch_window(gh, "sig-node", a, b) == 3
    assert {c[1]["q"] for c in fake.calls} == {k[0] for k in t}


def test_token_only_in_the_header_never_in_url_body_or_logs(tmp_path, monkeypatch, caplog):
    q = G.search_string("sig-apps", date(2025, 1, 1), date(2025, 1, 31))
    gh, fake, _ = client(tmp_path, monkeypatch, {(q, None): page([pr(1)], 1)})
    with caplog.at_level(logging.DEBUG):
        G.fetch_window(gh, "sig-apps", date(2025, 1, 1), date(2025, 1, 31))
    url, variables, headers = fake.calls[0]
    assert headers["Authorization"] == "Bearer SECRETTOKEN" and "SECRETTOKEN" not in url and "SECRETTOKEN" not in json.dumps(variables)
    assert "SECRETTOKEN" not in caplog.text and logging.getLogger("httpx").level >= logging.WARNING
    assert all("SECRETTOKEN" not in p.read_text() for p in tmp_path.glob("*.json"))


def test_sleeps_until_the_window_resets_when_the_budget_is_nearly_used(tmp_path, monkeypatch):
    q = G.search_string("sig-storage", date(2025, 1, 1), date(2025, 1, 31))
    t = {(q, None): page([pr(1)], 2, nxt="c1", remaining=40), (q, "c1"): page([pr(2)], 2)}
    gh, fake, sleeps = client(tmp_path, monkeypatch, t)
    gh.reset_at = None
    # reset_at in the response is 2030; "now" is 2026 -> a huge but bounded sleep is requested before the second page
    G.fetch_window(gh, "sig-storage", date(2025, 1, 1), date(2025, 1, 31))
    assert gh.stats["waits"] == 1 and sleeps and sleeps[0] > 3600


def test_rate_limited_responses_are_waited_out_then_retried(tmp_path, monkeypatch):
    q = G.search_string("sig-storage", date(2025, 1, 1), date(2025, 1, 31))
    seq = [403, page([pr(1)], 1)]

    def post(url, json=None, headers=None, timeout=None):
        a = seq.pop(0)
        return httpx.Response(a, text="rate", request=httpx.Request("POST", url)) if isinstance(a, int) else httpx.Response(200, json=a, request=httpx.Request("POST", url))

    monkeypatch.setattr(G.httpx, "post", post)
    sleeps = []
    gh = G.GithubGraphQL("SECRETTOKEN", tmp_path, sleep=sleeps.append, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert G.fetch_window(gh, "sig-storage", date(2025, 1, 1), date(2025, 1, 31)) == 1 and sleeps and sleeps[0] >= 60


def test_other_client_errors_raise_without_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(G.httpx, "post", lambda *a, **k: httpx.Response(401, text="Bad credentials SECRETTOKEN", request=httpx.Request("POST", "https://x")))
    gh = G.GithubGraphQL("SECRETTOKEN", tmp_path, sleep=lambda s: None)
    with pytest.raises(G.GithubError) as e:
        gh.query({"q": "x", "after": None, "n": 1})
    assert "SECRETTOKEN" not in str(e.value)


def test_normalize_dedupes_merges_sigs_lowercases_and_flags_truncation(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(page([pr(7, sigs=("sig/storage",)), pr(8, files=("a/b.go",), total_files=250, reviews=(("Carol", "COMMENTED"),))], 2)["data"]))
    (tmp_path / "b.json").write_text(json.dumps(page([pr(7, sigs=("sig/node", "kind/bug"))], 1)["data"]))
    rows = G.normalize(tmp_path)
    assert [r["number"] for r in rows] == [7, 8]
    r7, r8 = rows
    assert r7["sigs"] == ["sig/node", "sig/storage"] and r7["author"] == "bob" and r7["merged_by"] == "merger"
    assert r7["dirs"] == ["", "pkg/volume"] and r7["reviews"][0] == {"login": "alice", "state": "APPROVED", "at": "2025-01-02T00:00:00Z"}
    assert r8["files_truncated"] and r8["files_total"] == 250 and r8["reviews"][0]["login"] == "carol" and not r8["reviews_truncated"]


def test_base_branch_is_kept_and_an_older_cache_cannot_erase_it(tmp_path):
    new = pr(9)
    new["baseRefName"] = "release-1.31"
    old = pr(9)
    del old["baseRefName"]
    (tmp_path / "a_new.json").write_text(json.dumps(page([new], 1)["data"]))
    (tmp_path / "z_old.json").write_text(json.dumps(page([old], 1)["data"]))  # sorts later, so it is read last
    assert G.normalize(tmp_path)[0]["base_ref"] == "release-1.31"


def test_redact_emails_keeps_name_and_drops_address():
    from lineage.ingest.github_prs import redact_emails

    assert redact_emails("Co-authored-by: Jordan Liggitt <liggitt@google.com> done") == "Co-authored-by: Jordan Liggitt <<email removed>> done"
    assert redact_emails("see @user and github.com/x/y") == "see @user and github.com/x/y"
    assert redact_emails("go toolchain@v0.0.1-go1.25.7.linux-amd64 ok") == "go toolchain@v0.0.1-go1.25.7.linux-amd64 ok"  # not an address
