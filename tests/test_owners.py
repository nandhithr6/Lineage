from lineage.ingest.owners import chain, collapse, owners_path, parse_aliases, parse_owners


def test_parse_owners_basic_and_case_and_comments():
    f = parse_owners("""
approvers:
- Alice
- sig-docs-en-owners
# - commented-out
reviewers:
- '@Bob'
emeritus_approvers:
- carol
options:
  no_parent_owners: true
labels:
- sig/docs
""")
    assert f.no_parent_owners and f.labels == ["sig/docs"]
    assert ("approvers", "alice", "") in f.entries and ("approvers", "sig-docs-en-owners", "") in f.entries
    assert ("reviewers", "bob", "") in f.entries and ("emeritus_approvers", "carol", "") in f.entries
    assert not any("commented" in e[1] for e in f.entries)


def test_parse_owners_filters_keep_the_regex():
    f = parse_owners("filters:\n  '.*':\n    approvers: [a]\n  '\\.go$':\n    reviewers: [b]\n")
    assert f.has_filters and ("approvers", "a", ".*") in f.entries and ("reviewers", "b", "\\.go$") in f.entries


def test_parse_owners_bad_input_is_reported_not_raised():
    assert parse_owners("approvers: [unclosed").issues
    assert parse_owners("- just\n- a list").issues
    assert parse_owners("approvers: [1, ok]").issues  # non-string entry ignored with a note


def test_aliases_lowercased():
    assert parse_aliases("aliases:\n  Sig-Docs-Owners:\n    - Alice\n    - '@Bob'\n") == {"sig-docs-owners": ["alice", "bob"]}


def test_chain():
    assert chain("") == [""] and chain("a/b/c") == ["", "a", "a/b", "a/b/c"]
    assert owners_path("") == "OWNERS" and owners_path("a/b") == "a/b/OWNERS"


def test_collapse_runs_and_gaps():
    order = [130, 131, 132, 133, 134]
    presence = {130: {"x", "y"}, 131: {"x", "y"}, 132: {"y"}, 133: {"x", "y"}, 134: {"x", "y"}}
    runs = collapse(presence, order)
    assert runs["y"] == [(130, None)]  # present throughout: still true
    assert runs["x"] == [(130, 132), (133, None)]  # left at 132, came back at 133: two intervals
    assert collapse({130: {"z"}, 131: set()}, [130, 131])["z"] == [(130, 131)]
