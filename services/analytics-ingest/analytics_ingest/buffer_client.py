"""analytics_ingest.buffer_client — analytics-ingest's OWN Buffer GraphQL client.

C-BUFFER ruling: the repo's existing Buffer MCP tool server's tools.yaml
exposes exactly 3 tools (list_queue/get_post/create_draft), none of which
carry any engagement/metrics field. analytics-ingest therefore builds its
own direct Buffer GraphQL client, reusing the buffer-api-key secret — it
NEVER imports or calls through that other server's tool surface (AC-07);
this module has no import of, or reference to, that server's package name
anywhere.

ASSUMED_METRIC_FIELDS below is a best-effort design pending live
verification: user amendment 2 / AC-34 requires the Buffer GraphQL query
shape to be verified LIVE via introspection against api.buffer.com before
any live nightly run — see buffer_introspect.py, wired as a gated one-shot
Container Apps Job (infra/modules/analytics/buffer-smoke-job.bicep) that
runs separately from, and never gates, CI (fixture tests remain the sole
CI/PR gate).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from analytics_ingest.credentials import is_live_mode, resolve_secret

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

BUFFER_API_KEY_ENV = "BUFFER_API_KEY"
BUFFER_API_KEY_SECRET = "buffer-api-key"

# Field names this client's GraphQL query assumes exist on Buffer's real
# post-performance-metrics type. Confirmed/refuted live by
# buffer_introspect.py's assert_expected_fields() — this list is what that
# smoke test checks the live schema against, and what get_post_performance_day's
# query below requests.
ASSUMED_METRIC_FIELDS = [
    "id",
    "impressions",
    "reactions",
    "comments",
    "shares",
    "clicks",
]

_POST_PERFORMANCE_QUERY = """
query PostPerformanceDay($organizationId: ID!, $day: Date!) {
  organization(id: $organizationId) {
    posts(day: $day) {
      id
      impressions
      reactions
      comments
      shares
      clicks
      utmCampaign
      archetype
    }
  }
}
"""


def buffer_api_url() -> str:
    return os.environ.get("BUFFER_API_URL", "https://api.buffer.com/graphql")


def organization_id() -> str | None:
    return os.environ.get("BUFFER_ORG_ID")


def is_buffer_live_mode() -> bool:
    return is_live_mode(BUFFER_API_KEY_ENV, BUFFER_API_KEY_SECRET)


async def get_post_performance_day(day: str) -> list[dict[str, Any]]:
    """Fetch Buffer post-performance rows for `day` (YYYY-MM-DD).

    Only reachable in live mode (BUFFER_API_KEY resolvable); callers in
    fixture mode should read tests/fixtures/buffer_<day>.json instead
    (see analytics_ingest.ingest.ingest_buffer_day).
    """
    api_key = resolve_secret(BUFFER_API_KEY_ENV, BUFFER_API_KEY_SECRET)
    if not api_key:
        raise RuntimeError("get_post_performance_day called without a live BUFFER_API_KEY")

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "query": _POST_PERFORMANCE_QUERY,
        "variables": {"organizationId": organization_id(), "day": day},
    }
    async with httpx.AsyncClient(base_url=buffer_api_url()) as client:
        resp = await client.post("", json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", {}).get("organization", {}).get("posts", [])


async def get_buffer_day(day: str) -> list[dict[str, Any]]:
    """Dual-mode dispatch: the live Buffer GraphQL call when BUFFER_API_KEY
    is resolvable (Buffer is the one live-capable source this session),
    else the bundled fixture (tests/fixtures/buffer_<day>.json)."""
    if is_buffer_live_mode():
        return await get_post_performance_day(day)
    fixture_path = _FIXTURES_DIR / f"buffer_{day}.json"
    if not fixture_path.exists():
        return []
    return json.loads(fixture_path.read_text(encoding="utf-8"))
