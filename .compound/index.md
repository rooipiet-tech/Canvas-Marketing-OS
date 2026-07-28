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
- L-0011 [security] RBAC-mode Key Vault grants NO data-plane access by default, not even to subscription Owner — human operators need an explicit Key Vault Secrets Officer role assignment (Bicep role assignment or documented az role assignment create command). (active)
- L-0012 [security] publicNetworkAccess=Disabled on Key Vault ignores IP firewall rules entirely (Disabled removes the public endpoint regardless of networkAcls) — human secret-loading needs a temporary Enabled+Deny+IP-allowlist window, restored after; a gated in-VNet secret-loading Container Apps Job would avoid opening the vault at all. (active)
- L-0013 [conventions] A reviewer finding that's really a BUG CLASS (a mechanism reused across call sites, e.g. raw request content leaking into client-facing messages) needs the patch to audit every call site of that mechanism, not just the reported one — single-instance fixes reliably leave siblings for the next round. (active)
- L-0014 [conventions] Local tests (deep repo checkouts, caplog/monkeypatch fixtures) can mask deployment-only bugs: filesystem-depth-assuming path code and startup-configured process state (logging) both "pass" locally while failing at the real shallow-container entrypoint. Test at the actual target layout/entrypoint, not a convenient local one. (active)
- L-0015 [conventions] GOAL text with generic placeholder paths that don't match a repo's real directory conventions needs an explicit locked-decision negotiation before spec-writer freezes touch-scope — resolve at the research stage, not mid-build. (active)
- L-0016 [conventions] On this Windows dev machine: hash-based frozen-contract verify scripts must compare git blob content (`git show HEAD:<path>`), not raw working-tree bytes, since core.autocrlf=true converts committed LF to CRLF locally; bare `pytest` needs the pip user Scripts dir on PATH (bare `python` doesn't); check locally-installed interpreter versions each session before assuming one is missing for a requires-python pin. (active)
