# Human verification guide (what to check, how, and how long)

The hackathon rules say we must understand and verify what AI helped build, and every number below was produced with AI help. Until a person has checked an item, it is a **draft**
and must not be quoted as a result. `scripts/verify.py` walks you through each item, shows the evidence and the source link, and writes YOUR verdict into the file (saved after every answer,
so you can stop and resume any time). Standard library only; run it in a terminal from the repo root.

```bash
python3 scripts/verify.py status      # progress on everything
python3 scripts/verify.py links --sample 25
python3 scripts/verify.py report      # precision of the confident links from your verdicts
```

At each prompt: **y** yes/correct, **n** no/wrong, **u** unsure, **s** skip for now, **q** quit. "Unsure" is left out of every percentage, so use it freely rather than guessing.

## In priority order (if time is short, do them in this order)

| # | Command | Items | Time | What you are deciding |
|---|---|---|---|---|
| 1 | `verify.py semantic` | 4 | 5 min | Are the 4 links the embedding signal would add really right? If all 4 are **y**, the signal can be switched on (tell Claude/Abiram; it is one flag and a re-run). If any is **n**, leave it off. |
| 2 | `verify.py findings` | 3 | 5 min | Is each reported finding really a stale label? Open the docs page link, read the badge, open the gate file link, read the stages. This is the number the whole project is about. |
| 3 | `verify.py answers` | 7 | 15 min | The 6 saved Ask answers (and the saved plain-retrieval one): is every claim in the answer backed by the evidence rows shown, and does it answer the question? Open 2-3 of the source links per answer. |
| 4 | `verify.py links --sample 25` | 25 of 73 | 30-40 min | Is the docs label really about the linked feature gate? (details below). The AI's own "wrong" ones are always included. Do all 73 (about 1.5 hours) if you have the time: the interval gets tighter. |
| 5 | `verify.py prs --sample 20` | 20 of 40 | 20 min | Does the pull request really belong to the design document (KEP) it was linked to? |
| 6 | `verify.py gold` | 42 | 45-60 min | Is the expected answer of each draft eval question right? Abiram can do this one. |

Then run `python3 scripts/verify.py report` and `git diff --stat` (the verified files change), commit them, and tell Claude or Abiram to put the human-verified numbers in the README
(for example "confident links: 69 of 73 correct by the AI, 22 of 25 by a human check, interval 69-95%").

## How to judge each kind of item

**Links** (`eval/link_audit.jsonl`): the linker guessed which feature gate a docs label talks about, because the label itself does not name it. You see the docs section (heading, text, the badge) and the gate (description, stages).
- **y** if the section is clearly about the behaviour that gate switches on or off.
- **n** if the section is about something else, or about a *related but different* feature (for example the section is about a sub-feature that has its own gate, or the gate is a sibling such as `...FSQuotaMonitoring` when the section is about the main feature).
- **u** if you cannot tell after reading both. Do not spend more than two minutes on one.
- Also glance at the gate's first stage version: a label that says "alpha since v1.8" cannot be about a gate that first appeared in v1.15.

**PR to KEP links** (`eval/pr_kep_audit.jsonl`): the linker matched a pull request to a design document from what its author wrote. **y** only if the PR is part of the work the KEP describes (implements, graduates, tests or documents that feature).
A PR that merely mentions the KEP (for example in a list of someone's work) is **n**. Rows marked `weak` link only by a feature-gate name and are expected to be mostly mentions.

**Findings** (`eval/finding_audit.jsonl`): **y** if the docs badge really disagrees with the feature's real stage today (open the live docs page and the gate file to see). A label that is only about a *different version number* is a "minor" finding; judge the same way.

**Saved answers**: read the answer, then the evidence rows listed under it. **y** if every statement in the answer can be found in those rows (names, versions, roles), nothing is added from outside, and it answers the question asked. **n** if anything is
unsupported or wrong. The raw `**bold**` marks seen in older screenshots are cosmetic and fixed.

**Gold questions** (`eval/gold_review.md`): open the source link, check that the *expected* answer is exactly right (for ownership: the names in the OWNERS file; for time travel: the names at that release). **y** ticks the box. **n** marks it WRONG: then tell Claude, who fixes the question
in `eval/gold_questions.jsonl` and records the revision in `eval/README.md` (the gold set is a draft, which the README already says; every revision after seeing results is disclosed).

## What not to do
- Do not edit `eval/gold_heldout.jsonl` or `eval/results/heldout_freeze.json` (the frozen held-out set; a hash check will fail). The independent held-out set is a separate file the owner provides.
- Do not "fix" a draft by changing the AI's verdict field: write your verdict (the script does) and leave the AI's next to it, so the disagreement stays visible.
