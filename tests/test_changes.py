import json

from lineage.changes import build_digest, diff_digests, ownership_at, sample_for_date

SRC = [{"repo": "kubernetes/website", "commit": "a" * 40}]


def finding(key, claim, gate="G", stage="alpha", since="1.8", kind="stale_stage", sev="finding", page="Page A"):
    return {"key": key, "claim_id": claim, "gate": gate, "claimed_stage": stage, "claimed_since": since, "kind": kind, "severity": sev, "page_title": page, "section": "S", "message": "m"}


def digest(findings, claims=None, gates=None, keps=None, own=None, date="2026-01-01", sample=137):
    return {"snapshot_date": date, "sources": SRC, "ownership_sample": sample, "findings": {f["key"]: f for f in findings}, "claims": claims or {},
            "gates": gates or {}, "keps": keps or {}, "ownership": own or []}


def claim(state, since):
    return {"state": state, "since": since, "page": "p", "heading": "h"}


def test_fixed_upstream_is_resolved_with_the_reason():
    prev = digest([finding("k1", "c1")], claims={"c1": claim("alpha", "1.8")})
    cur = digest([], claims={"c1": claim("stable", "1.25")}, date="2026-02-01")
    d = diff_digests(prev, cur)
    (r,) = d["findings"]["resolved"]
    assert d["summary"]["findings_resolved"] == 1 and "fixed upstream" in r["why"] and "stable since v1.25" in r["why"]
    assert d["labels"] == [{"claim": "c1", "change": "edited", "was": "alpha since v1.8", "now": "stable since v1.25"}]


def test_a_gate_that_graduates_makes_a_label_go_stale():
    prev = digest([], claims={"c1": claim("beta", "1.20")}, gates={"G": {"removed": False, "stages": [["beta", "1.20", None]]}})
    cur = digest([finding("k1", "c1", stage="beta", since="1.20")], claims={"c1": claim("beta", "1.20")},
                 gates={"G": {"removed": False, "stages": [["beta", "1.20", "1.36"], ["stable", "1.37", None]]}})
    d = diff_digests(prev, cur)
    (n,) = d["findings"]["new"]
    assert "gate changed" in n["why"] and "beta -> stable" in n["why"] and "label stayed the same" in n["why"]
    assert d["gates"][0]["change"].startswith("beta -> stable")


def test_a_moved_section_is_not_reported_as_a_fix():
    prev = digest([finding("k1", "old#c", page="Old page")], claims={"old#c": claim("alpha", "1.8")})
    cur = digest([finding("k2", "new#c", page="New page")], claims={"new#c": claim("alpha", "1.8")}, date="2026-02-01")
    d = diff_digests(prev, cur)
    assert d["summary"]["findings_resolved"] == 0 and d["summary"]["findings_new"] == 0 and d["summary"]["findings_moved"] == 1
    assert d["findings"]["moved"][0]["from_page"] == "Old page" and d["findings"]["moved"][0]["to_page"] == "New page"


def test_unchanged_findings_persist_and_review_items_are_counted_apart():
    f = finding("k1", "c1")
    review = finding("k2", "c2", kind="possible_drift", sev="review")
    d = diff_digests(digest([f, review]), digest([f], date="2026-02-01"))
    assert d["summary"]["findings_persisting"] == 1 and d["summary"]["review_items_resolved"] == 1 and d["summary"]["findings_resolved"] == 0


def test_ownership_sampling_and_diff():
    edges = [{"repo": "kubernetes", "dir": "pkg/x", "role": "APPROVER_OF", "subject": "a", "valid_from": 130, "valid_to": 136},
             {"repo": "kubernetes", "dir": "pkg/x", "role": "APPROVER_OF", "subject": "b", "valid_from": 136, "valid_to": None}]
    assert ownership_at(edges, 135) == [["kubernetes", "pkg/x", "APPROVER_OF", "a"]]
    assert ownership_at(edges, 137) == [["kubernetes", "pkg/x", "APPROVER_OF", "b"]]
    dates = [{"num": 135, "date": "2025-12-17T00:00:00Z"}, {"num": 136, "date": "2026-04-22T00:00:00Z"}]
    assert sample_for_date(dates, "2026-01-01") == 135 and sample_for_date(dates, "2026-05-01") == 136 and sample_for_date(dates, "2026-05-01", latest=138) == 138
    d = diff_digests(digest([], own=ownership_at(edges, 135), sample=135), digest([], own=ownership_at(edges, 137), date="2026-02-01"))
    assert d["ownership"]["gained"][0]["who"] == "b" and d["ownership"]["lost"][0]["who"] == "a"


def test_build_digest_reads_a_snapshot_directory(tmp_path):
    (tmp_path / "doc_labels.jsonl").write_text(json.dumps({"id": "c1", "kind": "hardcoded", "state": "beta", "for_k8s_version": "1.20", "page_path": "p.md", "heading": "H"}) + "\n"
                                               + json.dumps({"id": "c2", "kind": "gate_lookup", "state": None, "for_k8s_version": None, "page_path": "p.md", "heading": "H"}) + "\n")
    (tmp_path / "gates.jsonl").write_text(json.dumps({"name": "G", "removed": False, "stages": [{"stage": "beta", "from_version": "1.20", "to_version": None}]}) + "\n")
    (tmp_path / "keps.jsonl").write_text(json.dumps({"id": "sig/1-x", "status": "implemented", "stage": "stable", "latest_milestone": "v1.30"}) + "\n")
    (tmp_path / "owners_edges.jsonl").write_text("")
    (tmp_path / "release_dates.jsonl").write_text(json.dumps({"num": 137, "date": "2026-08-26T00:00:00Z"}) + "\n")
    out = build_digest(tmp_path, [finding("k1", "c1")], "2026-10-15", SRC)
    assert set(out["claims"]) == {"c1"}  # only hard-coded labels can go stale
    assert out["gates"]["G"]["stages"] == [["beta", "1.20", None]] and out["keps"]["sig/1-x"]["status"] == "implemented" and out["ownership_sample"] == 137


def test_changes_since_endpoint_and_snapshots(client):  # noqa: F811
    from fastapi.testclient import TestClient  # noqa: F401

    snaps = client.get("/snapshots")
    assert snaps.status_code == 200 and snaps.json()["current"]["is_current"] is True
    r = client.get("/changes-since?since=latest")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"from", "to", "summary", "findings", "gates", "labels", "keps", "ownership", "caveats"}
    assert all("first_seen" in f for f in body["findings"]["persisting"])
    assert client.get("/changes-since?since=1999-01-01").status_code == 404


from tests.test_api import client  # noqa: E402,F401
from tests.test_loader import graph  # noqa: E402,F401
