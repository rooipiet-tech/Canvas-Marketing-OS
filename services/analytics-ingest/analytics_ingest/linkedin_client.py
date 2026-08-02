"""analytics_ingest.linkedin_client — dual-mode LinkedIn Community Management connector.

Uses a NEW, distinct Key Vault secret name
(linkedin-analytics-client-secret), never the existing coarse
linkedin-client-secret (which is scoped to publishing, not analytics —
AC-25). Falls back to its bundled fixture JSON
(tests/fixtures/linkedin_<day>.json) whenever is_live_mode() is False, and
is designed to skip live calls cleanly (no exception) when the secret is
absent (AC-27).

Live LinkedIn Community Management API calls are explicitly out of scope
for this build (see .loop/spec.json out_of_scope) —
is_linkedin_analytics_live_mode() exists so a future session can wire the
real call behind it without touching this module's fixture path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analytics_ingest.credentials import is_live_mode

LINKEDIN_ANALYTICS_CLIENT_SECRET_ENV = "LINKEDIN_ANALYTICS_CLIENT_SECRET"
LINKEDIN_ANALYTICS_CLIENT_SECRET_SECRET = "linkedin-analytics-client-secret"

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def is_linkedin_analytics_live_mode() -> bool:
    return is_live_mode(
        LINKEDIN_ANALYTICS_CLIENT_SECRET_ENV, LINKEDIN_ANALYTICS_CLIENT_SECRET_SECRET
    )


def get_linkedin_day(day: str) -> list[dict[str, Any]]:
    """Return LinkedIn rows for `day` (YYYY-MM-DD).

    Fixture mode (the only mode exercised this session): reads
    tests/fixtures/linkedin_<day>.json. Live mode is intentionally
    unimplemented — see ga4_client.get_ga4_day's docstring for the
    identical rationale.
    """
    fixture_path = _FIXTURES_DIR / f"linkedin_{day}.json"
    if not fixture_path.exists():
        return []
    return json.loads(fixture_path.read_text(encoding="utf-8"))
