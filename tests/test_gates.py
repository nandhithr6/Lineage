from pathlib import Path

from lineage.ingest.gates import expand_to_releases, parse_gate_file, release_num, version_key

SEED = """---
title: LocalStorageCapacityIsolation
content_type: feature_gate
stages:
  - stage: alpha
    defaultValue: false
    fromVersion: "1.7"
    toVersion: "1.9"
  - stage: beta
    defaultValue: true
    fromVersion: "1.10"
    toVersion: "1.24"
  - stage: stable
    defaultValue: true
    fromVersion: "1.25"
    toVersion: "1.26"
removed: true
---
Enable [local ephemeral storage](/docs/concepts/configuration/manage-resources-containers/)
and [emptyDir](/docs/concepts/storage/volumes/#emptydir).
"""

LIVE_OPEN = """---
title: SomeLiveGate
content_type: feature_gate
stages:
  - stage: beta
    defaultValue: false
    fromVersion: "1.32"
    toVersion: "1.32"
  - stage: beta
    defaultValue: true
    fromVersion: "1.33"
---
Body.
"""

REMOVED_OPEN = """---
title: DynamicProvisioningScheduling
content_type: feature_gate
stages:
  - stage: alpha
    defaultValue: false
    fromVersion: "1.11"
    toVersion: "1.11"
  - stage: deprecated
    fromVersion: "1.12"
removed: true
---
Body.
"""

INDEX = "---\ntitle: Feature Gates\ncontent_type: concept\n---\nOverview.\n"


def _gate(tmp_path: Path, name: str, text: str):
    p = tmp_path / f"{name}.md"
    p.write_text(text)
    return parse_gate_file(p, tmp_path, "abc123")


def test_version_helpers():
    assert version_key("1.25") > version_key("1.9")  # string compare would get this wrong
    assert release_num("1.25") == 125


def test_parse_seed_gate(tmp_path):
    g = _gate(tmp_path, "LocalStorageCapacityIsolation", SEED)
    assert g.removed and [s.stage for s in g.stages] == ["alpha", "beta", "stable"]
    assert g.stages[2].from_version == "1.25" and g.stages[2].to_version == "1.26"
    assert g.doc_links == [
        "/docs/concepts/configuration/manage-resources-containers/",
        "/docs/concepts/storage/volumes/",
    ]
    assert g.source_url == "https://github.com/kubernetes/website/blob/abc123/LocalStorageCapacityIsolation.md"
    assert g.issues == []


def test_expand_seed_gate_stage_per_release(tmp_path):
    rows = expand_to_releases(_gate(tmp_path, "LocalStorageCapacityIsolation", SEED))
    by_release = {r["release"]: r["stage"] for r in rows}
    assert by_release["1.7"] == "alpha" and by_release["1.9"] == "alpha"
    assert by_release["1.10"] == "beta" and by_release["1.24"] == "beta"
    assert by_release["1.25"] == "stable" and by_release["1.26"] == "stable"
    assert "1.27" not in by_release and len(rows) == 20  # 1.7 .. 1.26


def test_live_open_ended_runs_to_current(tmp_path):
    rows = expand_to_releases(_gate(tmp_path, "SomeLiveGate", LIVE_OPEN), current="1.37")
    assert rows[-1]["release"] == "1.37" and rows[-1]["default_value"] is True
    assert [r["release"] for r in rows][:2] == ["1.32", "1.33"]


def test_removed_open_ended_does_not_guess(tmp_path):
    g = _gate(tmp_path, "DynamicProvisioningScheduling", REMOVED_OPEN)
    assert any("removal release unknown" in i for i in g.issues)
    rows = expand_to_releases(g)
    assert [(r["release"], r["stage"]) for r in rows] == [("1.11", "alpha"), ("1.12", "deprecated")]


def test_non_gate_page_is_skipped(tmp_path):
    assert _gate(tmp_path, "index", INDEX) is None


OPEN_THEN_NEXT = """---
title: ContextualLogging
content_type: feature_gate
stages:
  - stage: alpha
    defaultValue: false
    fromVersion: "1.24"
  - stage: beta
    defaultValue: true
    fromVersion: "1.30"
---
Body.
"""


def test_open_stage_followed_by_next_ends_before_it(tmp_path):
    g = _gate(tmp_path, "ContextualLogging", OPEN_THEN_NEXT)
    assert any("end inferred" in i for i in g.issues)
    rows = expand_to_releases(g, current="1.37")
    by_release = {r["release"]: r["stage"] for r in rows}
    assert by_release["1.29"] == "alpha" and by_release["1.30"] == "beta" and by_release["1.37"] == "beta"
    assert len(rows) == len(by_release)  # exactly one stage per release
    assert {r["release"] for r in rows if r["inferred_end"]} == {f"1.{m}" for m in range(24, 30)}


OVERLAP = """---
title: PodHostIPs
content_type: feature_gate
stages:
  - stage: beta
    defaultValue: true
    fromVersion: "1.29"
    toVersion: "1.30"
  - stage: stable
    defaultValue: true
    fromVersion: "1.30"
    toVersion: "1.31"
  - stage: stable
    defaultValue: true
    fromVersion: "1.31.2"
    toVersion: "1.31"
---
Body.
"""


def test_overlap_keeps_both_stages_flagged_and_merges_exact_duplicates(tmp_path):
    rows = expand_to_releases(_gate(tmp_path, "PodHostIPs", OVERLAP))
    at_130 = sorted(r["stage"] for r in rows if r["release"] == "1.30")
    assert at_130 == ["beta", "stable"] and all(r["overlap"] for r in rows if r["release"] == "1.30")
    at_131 = [r for r in rows if r["release"] == "1.31"]
    assert len(at_131) == 1 and not at_131[0]["overlap"]  # duplicate stable rows merged
