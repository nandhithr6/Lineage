import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lineage.graph import ownership_views as ov
from lineage.graph.loader import load_all
from tests.test_api import client  # noqa: F401
from tests.test_loader import graph  # noqa: F401


def snap(tmp_path, **files):
    for name in ov.INPUTS:
        (tmp_path / name).write_text(files.get(name, "x"))
    (tmp_path / "manifest.json").write_text(json.dumps({"snapshot_date": "2026-10-15"}))
    return tmp_path


def test_fingerprint_changes_with_any_input(tmp_path):
    base = ov.fingerprint(snap(tmp_path))
    assert ov.fingerprint(tmp_path) == base
    for name in ov.INPUTS:
        (tmp_path / name).write_text("changed")
        assert ov.fingerprint(tmp_path) != base, name
        (tmp_path / name).write_text("x")
    assert ov.fingerprint(tmp_path) == base


def test_stored_views_are_used_only_for_the_same_snapshot_and_window(tmp_path):
    s = snap(tmp_path)
    path = tmp_path / "views.json"
    path.write_text(json.dumps({"fingerprint": ov.fingerprint(s), "months": 12, "views": {"orphans": [1]}}))
    assert ov.load_precomputed(12, path, s) == {"orphans": [1]}
    assert ov.load_precomputed(6, path, s) is None  # another window: compute live
    (tmp_path / "prs.jsonl").write_text("new data")
    assert ov.load_precomputed(12, path, s) is None  # the snapshot changed: the stored file is stale
    path.write_text("not json")
    assert ov.load_precomputed(12, path, s) is None and ov.load_precomputed(12, tmp_path / "missing.json", s) is None


def test_snapshot_today_is_the_manifest_date(tmp_path):
    assert ov.snapshot_today(snap(tmp_path)) == datetime(2026, 10, 15, tzinfo=timezone.utc)


def test_the_committed_file_matches_the_committed_snapshot():
    """Fails when snapshot data changes without re-running `python -m lineage.graph.ownership_views`, which would leave Insights showing stale numbers."""
    if not ov.VIEWS_PATH.exists():
        pytest.skip("no stored views")
    views = ov.load_precomputed(12)
    assert views is not None, "snapshot/ownership_views.json is stale: run uv run --env-file .env python -m lineage.graph.ownership_views"
    assert set(views) == {"orphans", "dormant", "gaps"} and views["gaps"]


def test_live_compute_has_the_same_shape_and_respects_the_window(graph):  # noqa: F811
    load_all(graph)
    v = ov.compute(graph, 12, datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert set(v) == {"orphans", "dormant", "gaps"} and all(isinstance(v[k], list) for k in v)
    far = ov.compute(graph, 12, datetime(2040, 1, 1, tzinfo=timezone.utc))
    assert far["orphans"] == [] or all(o["active_people"] > 0 for o in far["orphans"])  # a window far after any activity cannot invent activity


def test_the_api_serves_the_stored_file_when_it_matches(client, tmp_path, monkeypatch):  # noqa: F811
    import lineage.api.main as m

    fake = {"orphans": [{"path": "pkg/stored", "listed_approvers": 3, "active_people": 4, "months": 12, "unlisted_active": ["zed"]}], "dormant": [], "gaps": []}
    path = tmp_path / "views.json"
    path.write_text(json.dumps({"fingerprint": ov.fingerprint(), "months": 12, "views": fake}))
    monkeypatch.setattr(m, "OWNERSHIP_VIEWS_PATH", path)
    m._CACHE.clear()
    body = client.get("/insights?months=12").json()
    assert [o["path"] for o in body["orphaned"]] == ["pkg/stored"] and any("snapshot date" in n for n in body["notes"])
    m._CACHE.clear()
    assert client.get("/insights?months=6").status_code == 200  # another window is computed live
