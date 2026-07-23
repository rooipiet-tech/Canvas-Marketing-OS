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
- L-0002 [known-hard] GitHub Environments' required-reviewers rule cannot be self-approved by the triggering account — name a second reviewer identity at spec time or split "gate configured" from "gated deploy completed". (known-hard)
- L-0003 [conventions] A shell variable inside a double-quoted arg passed to a nested interpreter (e.g. python -c "...$id...") gets shell-expanded before the inner interpreter runs — single-quote or heredoc when the literal must not be shell-expanded. (active)
- L-0004 [security] Never generate a secret with `openssl rand -base64` for direct URI embedding — base64's `/+=` breaks URI parsing ~35-40% of the time; use hex or base64url instead. (active)
- L-0005 [conventions] Grep/regex-based spec verify commands cannot catch runtime/semantic bugs (lint failures, unsafe secret encoding) — keep a mandatory post-build multi-lens review distinct from spec verify commands. (active)
- L-0006 [architecture] Standing plan-reviewer checklist for IaC/CI builds: explicit workflow triggers, in-VNet verification for private-endpoint resources, bounded polling, explicit credential-flow, resolve-then-JSON gh api pattern, explicit IaC relative-path base dirs. (active)
