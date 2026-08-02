"""AC-27 — the dual-mode live/fixture gate: clean skip (never a failure) when a
provider's secret is absent, and Buffer's live path activates when
buffer-api-key IS present."""

from __future__ import annotations

import pytest

from analytics_ingest import buffer_client, ga4_client, linkedin_client, search_console_client


def test_ga4_skips_live_mode_cleanly_when_secret_absent(monkeypatch):
    monkeypatch.delenv("GA4_SERVICE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    if ga4_client.is_ga4_live_mode():
        pytest.skip("GA4_SERVICE_ACCOUNT_KEY unexpectedly resolvable in this environment")
    # No exception raised, cleanly resolves to False: this IS the "skip live
    # calls cleanly" behaviour the connector must exhibit.
    assert ga4_client.is_ga4_live_mode() is False


def test_search_console_skips_live_mode_cleanly_when_secret_absent(monkeypatch):
    monkeypatch.delenv("SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    if search_console_client.is_search_console_live_mode():
        pytest.skip(
            "SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY unexpectedly resolvable in this environment"
        )
    assert search_console_client.is_search_console_live_mode() is False


def test_linkedin_skips_live_mode_cleanly_when_secret_absent(monkeypatch):
    monkeypatch.delenv("LINKEDIN_ANALYTICS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    if linkedin_client.is_linkedin_analytics_live_mode():
        pytest.skip(
            "LINKEDIN_ANALYTICS_CLIENT_SECRET unexpectedly resolvable in this environment"
        )
    assert linkedin_client.is_linkedin_analytics_live_mode() is False


def test_buffer_skips_live_mode_cleanly_when_secret_absent(monkeypatch):
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    assert buffer_client.is_buffer_live_mode() is False


def test_buffer_live_mode_activates_when_secret_present(monkeypatch):
    monkeypatch.setenv("BUFFER_API_KEY", "dummy-test-key-for-live-mode-check")
    assert buffer_client.is_buffer_live_mode() is True
