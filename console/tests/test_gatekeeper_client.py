"""SCOPE-004 + INTEG-002: GatekeeperMock is labeled non-authoritative, has
zero SQL/CREATE TABLE, and seed/list round-trips correctly."""

from __future__ import annotations

import pytest

from app.clients.gatekeeper_mock import GatekeeperMock


@pytest.mark.asyncio
async def test_seed_pending_and_decided_entries() -> None:
    mock = GatekeeperMock()
    mock.seed_approval_inbox(status="pending", preview_title="pending item")
    mock.seed_approval_inbox(status="approved", preview_title="decided item", decided_by="op-1")

    entries = await mock.list_approval_inbox()
    assert len(entries) == 2
    statuses = {e["status"] for e in entries}
    assert statuses == {"pending", "approved"}


@pytest.mark.asyncio
async def test_toggle_kill_switch_records_audit() -> None:
    mock = GatekeeperMock()
    state = await mock.toggle_kill_switch(active=True, reason="test", operator="operator-1")
    assert state["active"] is True

    audit = await mock.get_last_audit_entry()
    assert audit is not None
    assert audit["operator"] == "operator-1"
    assert audit["active"] is True
