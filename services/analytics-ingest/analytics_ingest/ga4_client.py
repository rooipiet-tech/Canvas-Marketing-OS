"""analytics_ingest.ga4_client — dual-mode GA4 connector.

Uses a NEW, distinct Key Vault secret name (ga4-service-account-key), never
the existing coarse google-oauth-client-secret (AC-25). Falls back to its
bundled fixture JSON (tests/fixtures/ga4_<day>.json) whenever
is_live_mode() is False, and is designed to skip live calls cleanly (no
exception) when the secret is absent (AC-27).

Live GA4 Data API calls are explicitly out of scope for this build (see
.loop/spec.json out_of_scope: "Live LinkedIn Community Management API,
GA4, or Search Console calls this session — fixture-first is mandatory
since no credentials exist yet") — is_ga4_live_mode() exists so a future
session can wire the real call behind it without touching this module's
fixture path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analytics_ingest.credentials import is_live_mode

GA4_SERVICE_ACCOUNT_KEY_ENV = "GA4_SERVICE_ACCOUNT_KEY"
GA4_SERVICE_ACCOUNT_KEY_SECRET = "ga4-service-account-key"

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def is_ga4_live_mode() -> bool:
    return is_live_mode(GA4_SERVICE_ACCOUNT_KEY_ENV, GA4_SERVICE_ACCOUNT_KEY_SECRET)


def get_ga4_day(day: str) -> list[dict[str, Any]]:
    """Return GA4 rows for `day` (YYYY-MM-DD).

    Fixture mode (the only mode exercised this session): reads
    tests/fixtures/ga4_<day>.json. Live mode is intentionally unimplemented
    — is_ga4_live_mode() being True with no real GA4 Data API call wired is
    a documented, explicit out-of-scope gap for this build; the dual-mode
    gate itself (AC-27) is satisfied by is_ga4_live_mode() never raising
    and always resolving cleanly to a boolean.
    """
    fixture_path = _FIXTURES_DIR / f"ga4_{day}.json"
    if not fixture_path.exists():
        return []
    return json.loads(fixture_path.read_text(encoding="utf-8"))
