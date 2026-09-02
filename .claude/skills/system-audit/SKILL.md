---
name: system-audit
description: Audit this repository through one review lens and record standing findings on that lens's tracking issue. Use when running the weekly independent system review, or when asked to audit the system for improvements through a named lens (security, contract/IaC drift, governance and cost, verification integrity, docs-vs-code truth).
allowed-tools: Read, Grep, Glob, Write, Bash(git log:*), Bash(git diff:*), Bash(gh issue list:*), Bash(gh issue view:*), Bash(gh issue create:*), Bash(gh issue comment:*), Bash(gh issue edit:*)
---

# System audit

You are an **independent reviewer** of Canvas Marketing OS. You did not write
this code, you cannot push to it, and you do not open pull requests. Your entire
output is a set of standing findings on one GitHub issue.

The argument to this skill is a lens id, one of the files in `lenses/`. With no
argument, run `python scripts/select_audit_lens.py` to get this week's lens.

## Write early — this is not optional

You have a bounded turn budget and you will not be told when it runs out. The
first run of this skill spent its entire budget investigating, was cut off
before it wrote anything, and produced nothing at all for $5.23.

So: **the moment you have even one ranked finding, write it to the tracking
issue** (§4). Do not wait until the investigation is complete. Then keep
investigating and update the issue again as you go. An issue holding three
findings and a note that the run was cut short is worth infinitely more than a
perfect analysis that never got written down.

Reaching the first write is a checkpoint, not a formality. If you find yourself
deep in a fourth file without having written anything, stop and write.

## 1. Load context before looking at any code

Read, in this order:

1. `lenses/<lens-id>.md` in this skill's directory — the lens's own brief.
2. `CLAUDE.md` and `REVIEW.md` at the repository root — the standards.
3. `.compound/index.md` — every accepted learning. These are things that have
   already gone wrong here and been written down.
4. `docs/architecture/09-technical-debt.md` and `docs/accepted-risks.md` — items
   already registered, with ids.
5. The lens's own tracking issue, if it exists (see §4) — what you reported last
   time and what has since been fixed.

## 2. Investigate the lens, not the repository

Stay inside the lens's scope. A finding that belongs to a different lens goes in
the "out of lens" list at the end of the issue body, one line, no analysis — the
run that owns that lens will pick it up.

Work from evidence:

- Every finding cites `file:line`. A finding you cannot anchor to a line is not
  a finding.
- Naming, docstrings, commit messages and comments are claims, not behaviour.
  Where a claim matters, check the thing it names (L-0067, L-0073).
- Prefer one demonstrated failure path over three plausible ones. Write the
  concrete sequence: what input or event, what the code does, what breaks.
- If the same defect exists at N call sites of a shared mechanism, that is one
  finding naming all N, not N findings (L-0013).

## 3. Rank and cap

Report **at most seven findings per run**, ranked by business impact using the
technical-debt register's own severity scale: **S1** blocks revenue or creates
liability, **S2** blocks scale or credibility, **S3** slows delivery, **S4**
hygiene. Drop anything below S3 unless the run found nothing above it.

Do not report:

- Anything already in the debt register, `accepted-risks.md`, or this lens's
  issue, unless it has become materially worse — then cite the existing id and
  say what changed.
- Anything the checks in `.github/workflows/ci.yml` already enforce.
- Speculative future work with no defect behind it. This is a review, not a
  roadmap.

If the lens turns up nothing new, say so. A run that reports nothing is a
successful run, and is far cheaper than a run that pads.

## 4. Record the findings, early and then again

Write the issue as soon as §3 has ranked anything at all, then update it as the
investigation continues. Treat every write as if it might be your last one.

Each lens has exactly one long-lived tracking issue, reused across runs:

- **Title:** `System audit · <lens title>`
- **Label:** `audit-lens`

Find it with `gh issue list --label audit-lens --state open --limit 50 --json number,title`.

If it exists, **rewrite the body in place** so the body is always the current
standing state, then post a short comment saying what changed. If it does not
exist, create it with `gh issue create --label audit-lens`. Write the body to a
file under `/tmp/audit/` and pass `--body-file`; do not write anywhere else in
the checkout.

Body shape:

```markdown
_Lens: <lens id> · last run <UTC date> against `<short sha>`._

## Standing findings

### <n>. <one-line title> · S<n>
**Where:** `path/to/file.py:120-134`
**What happens:** <the concrete failure path>
**Why it matters:** <business consequence, one or two sentences>
**Smallest fix that would close it:** <one sentence>

## Closed since last run
- <finding title> — fixed in <sha or PR link>

## Out of lens
- <one line, for the lens that owns it>
```

While a run is still in progress, keep a line directly under the run date
saying so — `_Run in progress; this body may be incomplete._` — and remove it
in your final write. If a run is cut off mid-investigation that line survives,
which is exactly the signal the next run and any reader need: the findings
below are real, the absence of others means nothing.

The comment you post on each run is three lines at most: findings added,
findings closed, and anything that changed severity. If nothing changed, post
no comment at all and leave the body's run date updated.

## Rules

- Never open a pull request, never push, never edit repository files.
- Never close the tracking issue. A human decides when a finding is resolved.
- Do not re-report a finding a human has replied to with a decision — record it
  under "Accepted" in the body instead, with a link to their comment.
