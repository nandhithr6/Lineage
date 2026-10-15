"""Build eval/drift_cases.jsonl: the hand-picked seed, controls and ambiguous case from CLAUDE.md §6.

Which cases exist, and what we EXPECT, is hand-written below. Everything else (exact shortcode, URL, stage
history) is read from the snapshot so nothing is typed by hand. `gate_verified` means we checked the gate file
ourselves; the Ingress and CRD controls name no single gate, so they stay unverified.

Run: uv run python -m eval.build_drift_cases
"""
from __future__ import annotations

import json
from pathlib import Path

from lineage.link.linker import read_jsonl

# (case id, page path suffix, line, expected gate or None, outcome, notes)
CASES = [
    ("seed_local_ephemeral", "concepts/policy/resource-quotas.md", 194, "LocalStorageCapacityIsolation", "positive",
     "Label says alpha since 1.8; gate is stable since 1.25 and removed. Expected finding: stale stage."),
    ("neg_namespace_labels", "concepts/overview/working-with-objects/namespaces.md", 151, "NamespaceDefaultLabelName", "negative", ""),
    ("neg_cronjob_timezone", "concepts/workloads/controllers/cron-jobs.md", 178, "CronJobTimeZone", "negative",
     "Same page also has a stable 1.21 label at line 19 about a different feature."),
    ("neg_pod_priority", "concepts/scheduling-eviction/pod-priority-preemption.md", 9, "PodPriority", "negative",
     "Label sits above the first heading, so its section is the page intro."),
    ("neg_pod_os", "concepts/workloads/pods/_index.md", 127, "IdentifyPodOS", "negative", ""),
    ("neg_raw_block", "concepts/storage/persistent-volumes.md", 1016, "BlockVolume", "negative", ""),
    ("neg_volume_snapshots", "concepts/storage/persistent-volumes.md", 1117, "VolumeSnapshotDataSource", "negative", ""),
    ("neg_fsgroup_policy", "tasks/configure-pod-container/security-context.md", 367, "ConfigurableFSGroupPolicy", "negative", ""),
    ("neg_ingress_api", "concepts/services-networking/ingress.md", 21, None, "negative",
     "GA API with no single gate (per CLAUDE.md); gate not verified. Must not produce a finding."),
    ("neg_crd_version_deprecation", "tasks/extend-kubernetes/custom-resources/custom-resource-definition-versioning.md", 273, None, "negative",
     "Gate unknown ('verify' in CLAUDE.md); not verified. Must not produce a finding."),
    ("amb_sa_token_projection", "tasks/configure-pod-container/configure-service-account.md", 383, None, "ambiguous",
     "Label stable v1.20. BoundServiceAccountTokenVolume is stable 1.22: related but different, so it must NOT be linked confidently."),
]


def main() -> None:
    labels = {(x["page_path"].removeprefix("content/en/docs/"), x["line"]): x for x in read_jsonl(Path("snapshot/doc_labels.jsonl"))}
    gates = {g["name"]: g for g in read_jsonl(Path("snapshot/gates.jsonl"))}
    out = []
    for cid, page, line, gate, outcome, notes in CASES:
        lab = labels.get((page, line))
        g = gates.get(gate) if gate else None
        out.append({
            "id": cid,
            "label_found": lab is not None,
            "page_url": lab["page_url"] if lab else None,
            "source_file": lab["page_path"] if lab else page,
            "line": line,
            "claim_id": lab["id"] if lab else None,
            "shortcode": lab["shortcode"] if lab else None,
            "claimed_stage": lab["state"] if lab else None,
            "claimed_since": lab["for_k8s_version"] if lab else None,
            "expected_gate": gate,
            "gate_verified": g is not None,
            "forbidden_confident_gate": "BoundServiceAccountTokenVolume" if outcome == "ambiguous" else None,
            "stage_history": [(s["stage"], s["from_version"], s["to_version"]) for s in g["stages"]] if g else None,
            "gate_removed": g["removed"] if g else None,
            "expected_outcome": outcome,
            "notes": notes,
        })
    Path("eval/drift_cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    print(f"{len(out)} cases written; all labels found in snapshot: {all(r['label_found'] for r in out)}")


if __name__ == "__main__":
    main()
