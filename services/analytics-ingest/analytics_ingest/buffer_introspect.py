"""analytics_ingest.buffer_introspect — live Buffer GraphQL schema verification.

Per user amendment 2 / AC-34 (L-0026/L-0064): the Buffer GraphQL query
shape buffer_client.py assumes (ASSUMED_METRIC_FIELDS) must be verified
LIVE via introspection against api.buffer.com before any live nightly run
— never assumed from docs/memory. This module is the gated one-shot smoke
mechanism (run by caj-analytics-buffer-smoke,
infra/modules/analytics/buffer-smoke-job.bicep, triggered at deploy time
like every other service's smoke test) — separate from, and never gating,
CI (fixture-based tests remain the sole CI/PR gate).

assert_expected_fields() fails LOUDLY — never a silent pass — listing
exactly which of buffer_client.ASSUMED_METRIC_FIELDS were not found among
any GraphQL type's field names in the live schema.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from analytics_ingest.buffer_client import ASSUMED_METRIC_FIELDS, buffer_api_url
from analytics_ingest.credentials import resolve_secret

IntrospectionQuery = """
query IntrospectionQuery {
  __schema {
    types {
      name
      fields {
        name
      }
    }
  }
}
"""


async def run_introspection() -> dict[str, Any]:
    """POST the IntrospectionQuery to api.buffer.com and return the raw response body."""
    api_key = resolve_secret("BUFFER_API_KEY", "buffer-api-key")
    if not api_key:
        raise RuntimeError("run_introspection called without a live BUFFER_API_KEY")

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(base_url=buffer_api_url()) as client:
        resp = await client.post("", json={"query": IntrospectionQuery}, headers=headers)
        resp.raise_for_status()
        return resp.json()


def assert_expected_fields(schema_json: dict[str, Any]) -> None:
    """Walk __schema.types[].fields[].name and fail loudly on any missing field.

    Raises AssertionError naming exactly which ASSUMED_METRIC_FIELDS were
    not found anywhere in the live schema — never silently passes on a
    mismatch.
    """
    all_field_names: set[str] = set()
    types = schema_json.get("data", {}).get("__schema", {}).get("types", [])
    for graphql_type in types:
        for field in graphql_type.get("fields") or []:
            name = field.get("name")
            if name:
                all_field_names.add(name)

    missing = [f for f in ASSUMED_METRIC_FIELDS if f not in all_field_names]
    if missing:
        raise AssertionError(
            "Buffer live GraphQL schema is missing assumed metric field(s): "
            f"{missing} — buffer_client.py's query shape must be updated to "
            "match the real live schema before any live nightly run."
        )


async def main() -> int:
    """Entrypoint for `python -m analytics_ingest.buffer_introspect` (caj-analytics-buffer-smoke)."""
    schema_json = await run_introspection()
    try:
        assert_expected_fields(schema_json)
    except AssertionError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 1
    print(json.dumps({"status": "ok", "checked_fields": ASSUMED_METRIC_FIELDS}))
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
