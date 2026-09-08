# Brand-safety calibration export — 2026-09-07

A frozen, reproducible export of **100 real, already-published** Canvas
Intelligence social posts, pulled live from the connected Buffer
organisation, run through the actual `services/registry/safety_suite.py`
checker rather than the manual, one-off spot-check `09-technical-debt.md`
TD-32 and `19-live-verification-log.md` V2 originally recorded. This is the
"run `safety_suite.py` over an export of real published posts" calibration
pass both of those entries call for.

See `docs/architecture/21-brand-safety-calibration-2026-09-07.md` for the
full write-up, aggregated results, and what changed relative to the prior
manual figures. This directory holds the raw inputs and the raw checker
output so the run can be repeated or audited byte-for-byte.

## How this export was pulled

Via the Buffer MCP connector, against the `Canvas Intelligence` organisation
(`68e5f2187fe9a5263a3509ab`), on 2026-09-07:

1. `get_account` → resolve the organisation.
2. `list_channels` → the 3 connected channels: LinkedIn (`canvas-intelligence`
   page), Facebook (`Canvas Intelligence` page), Twitter (`CanvasBI` profile).
3. `list_posts` with `status: ["sent"]`, `first: 100`,
   `sort: [{field: "dueAt", direction: "desc"}]`, no `channelIds` filter (all
   3 channels together) — the 100 most recently *sent* (i.e. actually
   published) posts across the whole organisation, spanning
   2026-03-04 to 2026-09-02.

Each post's `text` field was written verbatim to `posts/<NNN>-<service>-<buffer
post id>.md`, `NNN` being its rank in the pull (001 = most recently sent).
`manifest.json` carries, per file, the Buffer post id, channel service,
`sentAt` and `dueAt`. No post content was edited, retitled, or filtered —
including the ones that turn out clean.

This is real, already-public marketing copy Canvas Intelligence itself
published on LinkedIn, Facebook and Twitter/X; there is nothing confidential
in it.

## How to reproduce

```sh
python services/registry/safety_suite.py \
  --dir services/registry/fixtures/calibration/2026-09-07-buffer-export/posts
```

`safety_suite.py` is deterministic (regex + lexicon, no model call — see its
own module docstring), so this must print exactly `safety_suite_findings.txt`
on stderr (mod­ulo the path prefix used to invoke it) and exit 1, every time,
on any machine. That determinism was checked directly as part of producing
this fixture: re-running against the files at their final, committed path
reproduced `safety_suite_findings.txt` byte-for-byte.

## Contents

| File | What it is |
|---|---|
| `posts/*.md` | The 100 post bodies, verbatim, one file per post |
| `manifest.json` | Per-file metadata: Buffer post id, channel, `sentAt`, `dueAt` |
| `safety_suite_findings.txt` | The exact stderr `safety_suite.py` prints against `posts/`, captured whole (not summarised) for auditability |
