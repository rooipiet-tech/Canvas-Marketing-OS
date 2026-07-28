# Compound Learnings Index

One line per accepted learning: `<id> [<class>] <statement> (status)`. No entries yet — first run.

## LEARN-000: no prior learnings at spec-writing time

This is the first run of this build loop against this repository. At
spec-writing time, `.compound/learnings/<class>/` subdirectories existed but
were empty — there were no prior accepted learnings to encode as
learnings-as-criteria in `.loop/spec.json`. This is stated explicitly here
(per spec.json's `LEARN-000` criterion) rather than fabricating history.
Future sessions should append their accepted learnings above this note, one
line per learning, using the `<id> [<class>] <statement> (status)` format.

## Accepted learnings

- L-0001 [conventions] Probe tool/credential availability (gh, docker, admin rights) before freezing a Must-have criterion that depends on live external-CLI verification. (active)
- L-0002 [conventions] GitHub Environments' required-reviewers rule has a `prevent_self_review` flag — self-approval is a config choice (settable false), not an unconditional block; check it via `gh api` before assuming a second identity is needed. (active; corrected 2026-07-23 after user confirmed prevent_self_review=false works fine)
- L-0003 [conventions] A shell variable inside a double-quoted arg passed to a nested interpreter (e.g. python -c "...$id...") gets shell-expanded before the inner interpreter runs — single-quote or heredoc when the literal must not be shell-expanded. (active)
- L-0004 [security] Never generate a secret with `openssl rand -base64` for direct URI embedding — base64's `/+=` breaks URI parsing ~35-40% of the time; use hex or base64url instead. (active)
- L-0005 [conventions] Grep/regex-based spec verify commands cannot catch runtime/semantic bugs (lint failures, unsafe secret encoding) — keep a mandatory post-build multi-lens review distinct from spec verify commands. (active)
- L-0006 [architecture] Standing plan-reviewer checklist for IaC/CI builds: explicit workflow triggers, in-VNet verification for private-endpoint resources, bounded polling, explicit credential-flow, resolve-then-JSON gh api pattern, explicit IaC relative-path base dirs. (active)
- L-0007 [known-hard] Azure resource-provider/feature registration (e.g. WorkloadProfiles) is one-time and async, can exceed 10 min on first use — a bounded-poll timeout on the first deploy is expected/retriable, not a build defect; add a fast path for already-registered + print recovery commands on timeout. (known-hard)
- L-0008 [architecture] Design rule: free idempotent preflight steps (e.g. provider registration) live outside approval gates so retries are unattended; only spend-triggering steps sit behind the gate. Split into a separate ungated workflow. (active)
- L-0009 [conventions] GitHub Actions workflow_run cannot reference its own workflow ("cannot listen to itself") — implement retries as in-job bash loops, not self-triggering workflow_run + rerun. Cross-workflow workflow_run (A watching B) is unaffected. (active)
- L-0010 [known-hard] AFEC feature flags for long-GA capabilities (e.g. WorkloadProfiles) can wedge Pending indefinitely and may be vestigial — gate on provider registration + real deploy outcome, not the flag's state. (known-hard)
- L-0011 [security] GitHub OIDC's subject claim differs per context (ref/pull_request/environment:<name>) — register one federated credential per subject actually used, from the presented assertion not docs; adding an environment gate and its federated credential are one atomic change. (active)
- L-0012 [security] Azure Container Apps collapses `$$` to `$` in secret/env values (its own $(...) reference-resolution engine) — DDL with Postgres `DO $$...$$` blocks gets silently corrupted; base64-encode content that must survive byte-for-byte, decode in-container. CI that applies a file directly won't catch this. (active)
- L-0013 [domain-constraints] Lead positioning is CA-founded finance-grade trust for multi-entity CFOs, not generic BI framing; BuildSmart/Sage/Powerfleet are named product pillars, not generic case studies. (active)
- L-0014 [domain-constraints] Proof sourced from confidential sales decks (client names, sales-deck stats) requires written permission-register clearance before public use — "available on request" is NOT cleared; check must fail closed. (active)
