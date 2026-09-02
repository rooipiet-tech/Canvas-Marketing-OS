# Continuous independent review

This repository is written almost entirely by parallel agent sessions. Every
change therefore arrives without a second reader, verified by machine-written
checks that can themselves be wrong (`L-0005`, `L-0046`, `L-0051`, `L-0058`,
`L-0059`). The setup described here is that second reader: an independent
reviewer that reads the code, reports what it finds, and **cannot change
anything**.

It has three layers, cheapest first.

## Layer 0 — the standards, written down

| File | Read by | Purpose |
|---|---|---|
| `CLAUDE.md` | every Claude Code session, the PR reviewer, the auditor | Project context, how to run the checks, and the ten hard rules that have each already cost a live incident here. |
| `REVIEW.md` | the PR reviewer only | Severity calibration, skip rules, the evidence bar, and nit volume. Review-only, so it does not weigh on ordinary sessions. |

**Change what the reviewer flags by editing these two files, not the
workflows.** `REVIEW.md` reaches the finding and verification agents directly,
so a rule lands more reliably there than in `CLAUDE.md` prose.

## Layer 1 — deterministic ratchets

| Workflow | Trigger | What it does |
|---|---|---|
| `codeql.yml` | PR, push to `main`, Thursdays 04:00 UTC | CodeQL `security-extended` for Python. Style is left to `ruff`. |
| `dependency-audit.yml` | changes to any `requirements*.txt`, Thursdays 05:00 UTC | `pip-audit` over every committed requirements file. |
| `dependabot.yml` | Tuesdays | Grouped Actions and pip updates, capped at 3 and 5 open PRs. |

These exist so the agent reviewers never spend a token on a defect class a
scanner finds for free.

If `pip-audit` reports something you are accepting rather than fixing, add the
advisory id to `.github/pip-audit-ignore.txt` with a comment saying why and what
would close it — and if it has no closing condition, add it to
`docs/accepted-risks.md` too, so it is reviewed with everything else.

## Layer 2 — the per-PR reviewer

`claude-review.yml` runs on every human-authored PR and posts findings as inline
comments.

- **Bot PRs are skipped.** The action rejects bot actors, so a Dependabot PR
  would fail the run rather than be reviewed. CI and a human review those.
- **It cannot approve or merge.** Its `permissions:` block is
  `contents: read`, `pull-requests: read`, `issues: read`. The strongest action
  available to it is a comment.
- **A force-push cancels the run in progress** rather than paying for a review
  of a diff that no longer exists.

### The managed alternative

If your Claude organisation is on **Team or Enterprise**, you can instead enable
the managed **Code Review** product at
[claude.ai/admin-settings/claude-code](https://claude.ai/admin-settings/claude-code)
and delete `claude-review.yml`. It runs a multi-agent review with a verification
pass on Anthropic's infrastructure, posts a `Claude Code Review` check run with a
severity table, reads the same `CLAUDE.md` and `REVIEW.md`, and needs no
workflow file. It costs roughly $15–25 per review and offers three triggers:
once per PR, on every push, or manual via `@claude review`.

Given this repository's PR volume, start on **once per PR** and escalate only if
the findings justify it. Do not run both the managed product and
`claude-review.yml` — you would pay twice for two reviews of the same diff.

## Layer 3 — the weekly system auditor

`claude-system-audit.yml` runs every Monday at 06:00 UTC and asks the question a
per-PR reviewer structurally cannot: *what has drifted across the system while
every individual change looked fine?*

Each run audits through **one lens**, chosen by ISO week:

| Lens | Asks |
|---|---|
| `01-security-and-data` | Secrets, permissions, PII, network posture, trust boundaries. |
| `02-contract-and-iac-drift` | Where does the running system differ from what the repository says it is? |
| `03-governance-cost-and-gates` | Can anything reach a publish without its gate? What can spend unbounded? |
| `04-verification-integrity` | Which of our green checks cannot go red? |
| `05-docs-vs-code-truth` | Where does `docs/architecture/` contradict the code? |

One lens per run keeps each run bounded and its cost predictable, and gives each
concern a **single long-lived tracking issue** (labelled `audit-lens`) that the
auditor rewrites in place, rather than a weekly wall of new issues.

The audit protocol itself lives in `.claude/skills/system-audit/SKILL.md`, so a
human can run the identical review locally:

```bash
claude
> /system-audit 02-contract-and-iac-drift
```

The lens briefs are `.claude/skills/system-audit/lenses/*.md`. To add one, drop
a file there with `id` and `title` front matter, add its id to
`claude-system-audit.yml`'s `workflow_dispatch` choice list, and run
`python scripts/select_audit_lens.py --self-test` — CI runs the same check, and
it fails if the two lists disagree or if the rotation would skip a lens.

To force a specific lens now, run the workflow manually from the Actions tab and
pick it from the dropdown.

### What keeps it independent

| Guarantee | How it is enforced |
|---|---|
| Cannot push, merge, or open a PR | `permissions: contents: read` — the token has no write access to code |
| Cannot approve a PR | The reviewer is not a repository reviewer; it comments |
| Cannot silently close its own findings | The skill forbids closing the tracking issue; a human decides |
| Cannot edit the checkout into the repository | It writes issue bodies under `/tmp/audit/`, and could not push them anyway |
| Bounded cost per run | `--max-turns 120`, `timeout-minutes: 45`, a single non-cancelling concurrency group |

### What a run costs, and what happens when it runs out

The first run (2026-09-02, security lens) exhausted a 60-turn budget in 9
minutes, cost **$5.23**, and produced nothing — it spent the whole budget
investigating and was cut off before writing anything down. Two things changed
as a result:

- The budget is now **120 turns**, so a normal run finishes. Expect roughly
  $8–12 per weekly run; the 45-minute `timeout-minutes` is the hard ceiling.
- More importantly, the skill now **writes the tracking issue as soon as it has
  ranked anything**, then updates it as it goes. A run that hits the ceiling
  now leaves its findings behind, marked `_Run in progress; this body may be
  incomplete._` The turn budget is a quality knob, not a pass/fail cliff.

If you want to spend less, lower `--max-turns` rather than removing lenses: a
shallower run still produces an issue.

## Setup

1. **Add the API key.** Create an `ANTHROPIC_API_KEY` repository secret from a
   key in the [Claude Console](https://platform.claude.com). Both workflows skip
   cleanly with a run notice when it is absent, so nothing goes red before you
   set it.

   To authenticate with a Claude subscription instead, run `claude setup-token`,
   store the result as `CLAUDE_CODE_OAUTH_TOKEN`, and change the
   `anthropic_api_key:` input in both workflows to
   `claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}`.

2. **Install the Claude GitHub App** on this repository
   ([github.com/apps/claude](https://github.com/apps/claude)). The action
   authenticates as the app by default.

3. **Merge to `main`.** GitHub runs scheduled workflows only from the default
   branch, so the weekly audit does nothing until this lands there.

4. **Watch the first audit.** Run it manually from the Actions tab rather than
   waiting for Monday, and read the tracking issue it opens before trusting the
   schedule.

## Known caveats

- **Scheduled runs are attributed to a user** — usually whoever last edited the
  `cron` line. If that ever becomes a bot account, the action's human-actor
  check will reject the run and the audit will fail until the account is added
  to `allowed_bots`.
- **Fork PRs get no review** if this repository is public: GitHub withholds
  secrets from fork-triggered runs. The managed Code Review product handles
  those via an explicit `@claude review` comment.
- **A scheduled workflow in a public repository is disabled after 60 days
  without repository activity.** Not a concern while sessions are landing daily.
- **No model is pinned** in `claude-system-audit.yml`, deliberately. A model
  identifier that nothing in CI can validate is exactly the silent-staleness
  class `L-0026` records. Pin one only alongside a check that it is still live.

## Turning it off

Each layer is one file. Delete `claude-review.yml` to stop per-PR reviews,
`claude-system-audit.yml` to stop the weekly audit, `codeql.yml`,
`dependency-audit.yml` or `dependabot.yml` for the ratchets. `CLAUDE.md` is
worth keeping regardless — every Claude Code session in this repository reads
it.
