"""AC-18 — the live smoke path skips cleanly with no vault reach."""

from __future__ import annotations

import live_smoke


def test_live_smoke_skips_cleanly_without_a_credential(monkeypatch, capsys):
    monkeypatch.delenv(live_smoke.credential_env_name(), raising=False)

    exit_code = live_smoke.run_live_smoke()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "skip" in out.lower()
    assert live_smoke.SKIP_MESSAGE in out


def test_skip_message_names_the_reason():
    assert "vault" in live_smoke.SKIP_MESSAGE.lower()


def test_smoke_provider_is_resolved_through_routing_policy():
    route, provider = live_smoke.resolve_smoke_provider()
    assert route.tier == live_smoke.SMOKE_TIER
    assert hasattr(provider, "complete")
