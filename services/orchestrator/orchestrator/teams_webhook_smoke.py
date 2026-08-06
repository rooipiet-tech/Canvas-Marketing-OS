"""One-off Teams-webhook verification smoke (round 19j follow-up, 6 Aug 2026).

Answers one narrow, deterministic question: given TEAMS_WEBHOOK_URL is wired
(PR #76, errand 4) and model-gateway's Postgres-password-staleness bug is
fixed (PR #79, F-GATEWAY-SECRET-STALE-REVISION), does a genuine
request-approval-shaped gate-check call actually reach Teams as a real
Adaptive Card? The organic proof circuit (loop_e2e_smoke.py) has never yet
gotten this far in a single run, because draft-content's real LLM output has
-- correctly -- tripped Brand Steward QA on every real run so far (round
19b onward, F-SMOKE-GREEN-REDEFINITION). This script isolates the one
remaining unverified hop (gate-check -> Teams) from that probabilistic
content-quality gate, by calling ca-gatekeeper's real POST /gate-check
directly -- bypassing ingest-signals/draft-brief/draft-content/qa-review
(and every LLM call among them) entirely.

SECURITY NOTE, read before touching this file again: this deliberately
reuses the REAL, already-shipped `publish.social_post`/`publish` policy
entry (level 1, services/gatekeeper/policy/autonomy.yaml) -- the SAME one
production's request_approval_handler (dispatch.py) uses -- rather than
adding a new policy entry for a synthetic test function_id. See
infra/modules/governance/governance-smoke-test-job.bicep's RISK-01 comment
for exactly why: a dedicated smoke/test function_id pinned to an
auto-approve level used to exist in this repo and was a standing,
authentication-free auto-approve path (/gate-check is agent-native per
AC-17 -- anything reachable inside the VNet that knew the function_id could
drive it with no human in the loop). tests/test_policy.py enforces that no
shipped policy entry may be named smoke.*/test.*. Do not add a new
policy.yaml entry for this or any future one-off test; reuse the real,
already-governed entry instead, exactly as this script and
caj-governance-smoke both do.

Unlike caj-governance-smoke (AC-35), this script does NOT pre-seed an
approved governance.approval_inbox row -- that job intentionally does, to
bypass the human click and test sign/verify/replay instead. Pre-seeding
here would make gate_check take the "already approved" branch and skip
dispatch_approval_request (and therefore skip posting to Teams) entirely --
defeating the one thing this script exists to test. So this call uses a
fresh, never-before-seen (agent_run_id, content_hash) pair, guaranteed to
miss `latest_approved()`'s lookup, so /gate-check takes the real "no prior
approval -> create_approval_request -> dispatch_approval_request" branch --
the exact path a genuine, QA-cleared production run would take.

The card this posts is unambiguous and inert: preview_title is prefixed
"[TEST -- SAFE TO REJECT]" and evidence_summary explains why it exists;
content_hash is synthetic and not tied to any real Vault asset, so nothing
downstream (Publisher, any other consumer) can ever act on an approval or
rejection of this specific card either way.

Run inside caj-loop-e2e-smoke via
`python -m orchestrator.teams_webhook_smoke` -- triggered ONLY via
deploy-loop-e2e-smoke.yml's workflow_dispatch `mode: gate-check-only`
input, never on the workflow's normal workflow_run chain trigger, so this
can never fire as a side effect of a real deploy. Needs
CMOS_GATEKEEPER_BASE_URL set explicitly (the workflow resolves it via `az
containerapp show` before starting this execution) -- resolve_live_fqdn's
own az-CLI-subprocess fallback is not reliable inside this job's plain
orchestrator-image container, which has no az CLI installed.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

from orchestrator.clients.gatekeeper_client import (
    GatekeeperClient,
    GatekeeperClientError,
    resolve_gatekeeper_base_url,
)

# The SAME (function_id, action_class) pair production's request-approval
# handler uses (dispatch.py's REAL_PUBLISH_FUNCTION_ID/REAL_PUBLISH_ACTION_CLASS)
# -- see the module docstring's SECURITY NOTE for why this must never be a
# synthetic/test-only function_id.
FUNCTION_ID = "publish.social_post"
ACTION_CLASS = "publish"

EXPECTED_OUTCOME = "escalated"
EXPECTED_ROUTE = "teams"


def build_gate_check_kwargs(run_tag: str) -> dict[str, Any]:
    """Pure builder -- no I/O -- so the payload shape is unit-testable
    without a live gatekeeper. `run_tag` is injected (not generated here)
    for the same reason."""
    return {
        "agent_run_id": str(uuid.uuid4()),
        "function_id": FUNCTION_ID,
        "action_class": ACTION_CLASS,
        "content_hash": f"test-teams-webhook-verification-{run_tag}",
        "preview_title": (
            "[TEST -- SAFE TO REJECT] Teams webhook verification "
            "(round 19j follow-up, F-GATEWAY-SECRET-STALE-REVISION)"
        ),
        "preview_reference": f"test://teams-webhook-verification/{run_tag}",
        "evidence_summary": (
            "One-off deterministic test of the request-approval -> "
            "gate-check -> Teams-webhook path, called directly against "
            "ca-gatekeeper's real /gate-check endpoint. Bypasses "
            "ingest-signals/draft-brief/draft-content/qa-review (and their "
            "LLM calls) entirely -- this is not real drafted content and is "
            "not tied to any Vault asset. Approving or rejecting this card "
            "has no downstream effect either way. Triggered manually via "
            "deploy-loop-e2e-smoke.yml's mode=gate-check-only input."
        ),
    }


def evaluate_response(decision: dict[str, Any]) -> tuple[bool, str]:
    """Pure judgement over gate_check's response body -- unit-testable
    without a live gatekeeper. PASS iff outcome=='escalated' (level-1
    approval required, no prior approval found) AND
    approval_route=='teams' (TEAMS_WEBHOOK_URL resolved and the card was
    actually posted, not silently routed to the local inbox fallback)."""
    outcome = decision.get("outcome")
    route = decision.get("approval_route")

    if outcome != EXPECTED_OUTCOME:
        return False, (
            f"FAIL: expected outcome {EXPECTED_OUTCOME!r} (approval required, level 1) "
            f"but got {outcome!r} -- check services/gatekeeper/policy/autonomy.yaml's "
            f"{FUNCTION_ID}/{ACTION_CLASS} entry and check no kill switch is active."
        )
    if route != EXPECTED_ROUTE:
        return False, (
            f"FAIL: expected approval_route {EXPECTED_ROUTE!r} but got {route!r} -- "
            "TEAMS_WEBHOOK_URL is likely not resolving on ca-gatekeeper, or "
            "dispatch_approval_request fell back to the local inbox. Check "
            "ca-gatekeeper's TEAMS_WEBHOOK_URL env var / Key Vault secret."
        )
    return True, (
        "PASS: gate-check escalated for approval and routed via Teams -- "
        "a real Adaptive Card was posted."
    )


def main() -> int:
    base_url = resolve_gatekeeper_base_url()
    if not base_url:
        print(
            "FAIL: could not resolve ca-gatekeeper's base URL -- "
            "CMOS_GATEKEEPER_BASE_URL must be set explicitly for this script "
            "(see module docstring)"
        )
        return 1

    run_tag = uuid.uuid4().hex[:8]
    kwargs = build_gate_check_kwargs(run_tag)

    with GatekeeperClient(base_url=base_url) as gatekeeper:
        try:
            decision = gatekeeper.gate_check(**kwargs)
        except GatekeeperClientError as exc:
            print(f"FAIL: gate_check raised: {exc}")
            return 1

    print(f"gate_check response: {decision}")
    passed, message = evaluate_response(decision)
    print(message)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
