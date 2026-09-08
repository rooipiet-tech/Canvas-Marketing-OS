"""Unit tests for azure_client_lib.fqdn.resolve_live_fqdn (TD-16).

test_resolve_live_fqdn_never_raises_when_az_unavailable is carried forward
unchanged from services/orchestrator/tests/test_clients.py -- the one test
that existed for any of the three pre-extraction copies. The mocked-
subprocess cases below are new: none of the three original copies had a
test that exercised the success path, the non-zero-returncode path, or the
empty-stdout path in isolation, only indirectly via each service's own
FQDN-consuming clients.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from azure_client_lib.fqdn import resolve_live_fqdn


def test_resolve_live_fqdn_never_raises_when_az_unavailable():
    # In this sandbox `az` may or may not exist; either way this must
    # never raise, and must never fabricate a hostname.
    result = resolve_live_fqdn("ca-does-not-exist", timeout=2.0)
    assert result is None or result.startswith("https://")


def test_resolve_live_fqdn_returns_https_url_on_success(monkeypatch):
    fqdn = "ca-model-gateway.example.azurecontainerapps.io"

    def fake_run(*_args, **_kwargs):
        return MagicMock(returncode=0, stdout=fqdn + "\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_live_fqdn("ca-model-gateway") == f"https://{fqdn}"


def test_resolve_live_fqdn_returns_none_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: MagicMock(returncode=1, stdout="")
    )
    assert resolve_live_fqdn("ca-does-not-exist") is None


def test_resolve_live_fqdn_returns_none_on_empty_stdout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(returncode=0, stdout=""))
    assert resolve_live_fqdn("ca-not-yet-provisioned") is None


_SUBPROCESS_FAILURES = [FileNotFoundError(), subprocess.TimeoutExpired("az", 15), OSError()]


@pytest.mark.parametrize("exception", _SUBPROCESS_FAILURES)
def test_resolve_live_fqdn_returns_none_on_expected_subprocess_failures(monkeypatch, exception):
    def _raise(*_a, **_k):
        raise exception

    monkeypatch.setattr(subprocess, "run", _raise)
    assert resolve_live_fqdn("ca-model-gateway") is None


def test_resolve_live_fqdn_passes_resource_group_and_app_name_to_az_cli(monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="mcp-buffer.example.azurecontainerapps.io")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    resolve_live_fqdn("mcp-buffer", resource_group="cmos-dev")
    assert captured["cmd"][:3] == ["az", "containerapp", "show"]
    assert "-g" in captured["cmd"] and "cmos-dev" in captured["cmd"]
    assert "-n" in captured["cmd"] and "mcp-buffer" in captured["cmd"]
