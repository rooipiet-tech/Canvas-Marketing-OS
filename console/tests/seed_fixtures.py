"""Print a base64-encoded CONSOLE_SEED_FIXTURES_JSON_B64 payload.

Used by .github/workflows/ci.yml's console-accessibility job (GOAL-005/
GOAL-005b) to seed a locally-running mock-mode console before pointing
lighthouse/axe-core at it, and reusable for any local manual run:

    export CONSOLE_SEED_FIXTURES_JSON_B64=$(python tests/seed_fixtures.py)
    uvicorn app.main:app

Reuses the exact same seeding mechanism (app/seed.py's seed_from_env) that
deploy-console.yml's gated smoke job uses against the real deployed
console — not a second, divergent fixture format.
"""

from __future__ import annotations

import base64
import json

FIXTURES = {
    "agent_runs": [
        {
            "agent_name": "brief-drafter",
            "status": "running",
            "vertical": "mobility",
            "function_id": "brief-drafting-v1",
        },
        {
            "agent_name": "asset-generator",
            "status": "succeeded",
            "vertical": "finance",
            "function_id": "asset-generation-v1",
        },
    ],
    "assets": [
        {
            "asset_type": "social_post_image",
            "vertical": "mobility",
            "function_id": "asset-generation-v1",
            "evidence_grade": "A",
        },
        {
            "asset_type": "landing_page_copy",
            "vertical": "finance",
            "function_id": "asset-generation-v1",
            "evidence_grade": "B",
        },
    ],
    "costs": [
        {
            "agent_run_id": "fixture-run-1",
            "function_id": "brief-drafting-v1",
            "provider": "anthropic",
            "amount": "1.25",
            "incurred_at": "2026-07-28T09:00:00Z",
        },
        {
            "agent_run_id": "fixture-run-2",
            "function_id": "asset-generation-v1",
            "provider": "anthropic",
            "amount": "3.40",
            "incurred_at": "2026-07-28T10:00:00Z",
        },
    ],
    "approval_inbox": [
        {
            "status": "pending",
            "function_id": "asset-generation-v1",
            "preview_title": "Spring EV finance social post — pending review",
        },
        {
            "status": "approved",
            "function_id": "brief-drafting-v1",
            "preview_title": "Spring EV finance brief — approved",
            "decided_by": "operator@example.com",
        },
    ],
}


def main() -> None:
    encoded = base64.b64encode(json.dumps(FIXTURES).encode("utf-8")).decode("ascii")
    print(encoded)


if __name__ == "__main__":
    main()
