import json
from pathlib import Path

from lineage.ingest.keps import gate_regex, handles, normalise_status, readme_gate_mentions, resolve_refs


def test_handles_lowercased_and_placeholders_skipped():
    issues: list[str] = []
    assert handles(["@Thockin", "TBD", "Jane Doe", "@msau42"], issues, "author") == ["thockin", "msau42"]
    assert len(issues) == 1 and "Jane Doe" in issues[0]  # TBD is a known placeholder: silent; a bare name is logged


def test_status_typo_mapped_unknown_logged_template_not_mapped():
    issues: list[str] = []
    assert normalise_status("imlpemented", issues) == "implemented"
    assert normalise_status("implementable", issues) == "implementable"
    assert normalise_status("provisional|implementable|implemented", issues) is None
    assert normalise_status("removed", issues) is None
    assert any("typo" in i for i in issues) and sum("unmapped" in i for i in issues) == 2
    assert any("template placeholder" in i for i in issues)


def test_resolve_refs():
    known = {"sig-node/123-foo", "sig-api-machinery/0006-apply"}
    res, other = resolve_refs(["/keps/sig-node/123-foo", "/keps/sig-api-machinery/0006-apply.md",
                               "n/a", "https://github.com/kubernetes/enhancements/pull/9", "/keps/sig-node/999-gone"], known)
    assert res == ["sig-node/123-foo", "sig-api-machinery/0006-apply"]
    assert other == ["https://github.com/kubernetes/enhancements/pull/9", "/keps/sig-node/999-gone"]


def test_readme_gate_mentions_confidence_and_exclusion():
    rx = gate_regex(["LocalStorageCapacityIsolation", "PodPriority", "Pod"])
    text = "PodPriority is mentioned in passing here." + " x" * 80 + " Enable the LocalStorageCapacityIsolation feature gate."
    got = {m["gate"]: m["confidence"] for m in readme_gate_mentions(text, rx, set())}
    assert got == {"LocalStorageCapacityIsolation": 0.7, "PodPriority": 0.4}  # "Pod" alone never matches inside longer words
    assert readme_gate_mentions(text, rx, {"PodPriority"}) == [{"gate": "LocalStorageCapacityIsolation", "confidence": 0.7}]


def test_seed_kep_in_snapshot_if_present():
    path = Path("snapshot/keps.jsonl")
    if not path.exists():
        return
    k = {x["id"]: x for x in map(json.loads, path.read_text().splitlines())}["sig-storage/361-local-ephemeral-storage-isolation"]
    assert k["owning_sig"] == "sig-storage" and k["feature_gates"] == []
    assert [g["gate"] for g in k["readme_gates"]] == ["LocalStorageCapacityIsolation"]


def test_no_email_or_company_in_sigs_snapshot():
    path = Path("snapshot/sigs.jsonl")
    if not path.exists():
        return
    text = path.read_text()
    assert "@" not in text.replace("@kubernetes", "") or '"email"' not in text
    assert '"email"' not in text and '"company"' not in text


def test_nameless_feature_gate_entries_are_skipped_and_reported(tmp_path):
    from lineage.ingest import keps as K
    import subprocess
    repo = tmp_path / "enh"
    d = repo / "keps" / "sig-x" / "1-thing"
    d.mkdir(parents=True)
    (d / "kep.yaml").write_text("title: T\nkep-number: 1\nowning-sig: sig-x\nstatus: implemented\nfeature-gates:\n  - name: Real\n  - components: [a]\n  - name: ''\n")
    (d / "README.md").write_text("x")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    [k] = K.parse_keps(repo)
    assert k.feature_gates == ["Real"] and sum("without a name" in i for i in k.issues) == 2
