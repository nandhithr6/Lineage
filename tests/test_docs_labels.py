from pathlib import Path

from lineage.ingest.docs_labels import extract_labels, page_url, slugify

PAGE = """---
title: Resource Quotas
---

Intro text.

## Quota for local ephemeral storage

{{< feature-state for_k8s_version="v1.8" state="alpha" >}}

Uses [ephemeral storage](/docs/concepts/configuration/manage-resources-containers/) and
a [volume](/docs/concepts/storage/volumes/#emptydir).

## Another section

{{< feature-state state="stable" for_k8s_version="v1.24" >}}
{{< feature-state feature_gate_name="VolumeAttributesClass" >}}
{{< feature-state state="beta" >}}

```
{{< feature-state for_k8s_version="v1.1" state="alpha" >}}
```

{{</* feature-state state="stable" */>}}
"""


def _labels():
    return extract_labels(PAGE, "content/en/docs/concepts/policy/resource-quotas.md",
                          Path("concepts/policy/resource-quotas.md"), "abc123")


def test_counts_skip_code_fences_and_escaped_examples():
    labels = _labels()
    assert len(labels) == 4
    assert [lab.kind for lab in labels] == ["hardcoded", "hardcoded", "gate_lookup", "hardcoded"]


def test_seed_label():
    lab = _labels()[0]
    assert (lab.state, lab.for_k8s_version) == ("alpha", "1.8")  # leading v removed
    assert lab.heading == "Quota for local ephemeral storage" and lab.anchor == "quota-for-local-ephemeral-storage"
    assert lab.doc_links == [
        "/docs/concepts/configuration/manage-resources-containers/",
        "/docs/concepts/storage/volumes/",
    ]
    assert lab.page_title == "Resource Quotas" and lab.id.endswith("#quota-for-local-ephemeral-storage:1")
    assert lab.source_url == "https://github.com/kubernetes/website/blob/abc123/content/en/docs/concepts/policy/resource-quotas.md#L9"


def test_argument_order_does_not_matter():
    lab = _labels()[1]
    assert (lab.state, lab.for_k8s_version) == ("stable", "1.24")


def test_gate_lookup_has_no_hardcoded_fields():
    lab = _labels()[2]
    assert lab.gate_name == "VolumeAttributesClass" and lab.state is None and lab.for_k8s_version is None


def test_missing_version_is_flagged_not_guessed():
    lab = _labels()[3]
    assert lab.for_k8s_version is None and any("defaults to the docs site version" in i for i in lab.issues)


def test_helpers():
    assert slugify("Quota for `local` ephemeral storage!") == "quota-for-local-ephemeral-storage"
    assert page_url(Path("concepts/policy/_index.md")) == "https://kubernetes.io/docs/concepts/policy/"
    assert page_url(Path("tasks/run/job.md")) == "https://kubernetes.io/docs/tasks/run/job/"
