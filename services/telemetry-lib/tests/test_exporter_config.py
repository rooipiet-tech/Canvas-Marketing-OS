"""TELE-001: exporter setup reads connection string from config/env, never
falls back to a hardcoded global endpoint."""

from __future__ import annotations

import pytest

from telemetry_lib.exporter import CONNECTION_STRING_ENV_VAR, configure_tracer_provider


def test_exporter_config_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONNECTION_STRING_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError):
        configure_tracer_provider()


def test_exporter_config_configures_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_connection_string = (
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://southafricanorth-1.in.applicationinsights.azure.com/"
    )
    monkeypatch.setenv(CONNECTION_STRING_ENV_VAR, fake_connection_string)
    provider = configure_tracer_provider()
    assert provider is not None


def test_exporter_config_explicit_arg_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONNECTION_STRING_ENV_VAR, raising=False)
    fake_connection_string = (
        "InstrumentationKey=11111111-1111-1111-1111-111111111111;"
        "IngestionEndpoint=https://southafricanorth-1.in.applicationinsights.azure.com/"
    )
    provider = configure_tracer_provider(connection_string=fake_connection_string)
    assert provider is not None
