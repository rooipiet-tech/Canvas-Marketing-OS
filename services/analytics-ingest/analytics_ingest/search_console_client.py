"""analytics_ingest.search_console_client — dual-mode Search Console connector.

Uses a NEW, distinct Key Vault secret name
(search-console-service-account-key), never the existing coarse
google-oauth-client-secret (AC-25). Falls back to its bundled fixture JSON
(tests/fixtures/search_console_<day>.json) whenever is_live_mode() is
False, and is designed to skip live calls cleanly (no exception) when the
secret is absent (AC-27).

Live Search Console API calls are explicitly out of scope for this build
(see .loop/spec.json out_of_scope) — is_search_console_live_mode() exists
so a future session can wire the real call behind it without touching this
module's fixture path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analytics_ingest.credentials import is_live_mode

SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_ENV = "SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY"
SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_SECRET = "search-console-service-account-key"

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def is_search_console_live_mode() -> bool:
    return is_live_mode(
        SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_ENV, SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_SECRET
    )


def get_search_console_day(day: str) -> list[dict[str, Any]]:
    """Return Search Console rows for `day` (YYYY-MM-DD).

    Fixture mode (the only mode exercised this session): reads
    tests/fixtures/search_console_<day>.json. Live mode is intentionally
    unimplemented — see ga4_client.get_ga4_day's docstring for the
    identical rationale.
    """
    fixture_path = _FIXTURES_DIR / f"search_console_{day}.json"
    if not fixture_path.exists():
        return []
    return json.loads(fixture_path.read_text(encoding="utf-8"))
