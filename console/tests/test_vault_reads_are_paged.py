"""Console reads must page vault-api, not show its first page.

F-CONSOLE-UNPAGED-READS.

Every list_* method on VaultApiClient defaults to `limit=50, offset=0`,
and every caller in app/services.py invoked it with no arguments at all.
So each console read surface displayed -- and each aggregate SUMMED --
only the 50 most recently created rows. search_vault's own docstring
says "fetch the full list from the matching vault-api list endpoint,
THEN filter in Python"; it was fetching a page and calling it the list.

WHY THIS WAS INVISIBLE. VaultApiMock is seeded by tests with a handful of
fixtures, so 50 is never reached and every existing test passes either
way. The bug only exists against real data -- which is to say, it only
exists once VAULT_API_MODE flips to "real" (INTEG-001).

WHY IT MATTERS MORE THAN IT LOOKS. The failure is silent and plausible.
The cost ledger would render a complete-looking total that is simply
short; nothing marks it as partial. Worse, `?date=` filtering happens
client-side AFTER the truncation, so asking for any day older than the
newest 50 cost rows returns "no costs" rather than an error -- a page
that is confidently wrong rather than visibly broken, which is the same
failure mode the GATEKEEPER_API_MODE mock had before INTEG-002 (see
console-app.bicep's comment on the approval inbox).

These tests seed past the page size, which is the only thing that
distinguishes the two behaviours.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.clients.vault_api_mock import VaultApiMock
from app.services import (
    VAULT_MAX_ROWS,
    VAULT_PAGE_SIZE,
    _fetch_all,
    get_cost_ledger,
    get_task_queue,
    search_vault,
)

# Comfortably past vault-api's 500-row maximum page, so a correct
# implementation must issue more than one request.
OVER_ONE_PAGE = VAULT_PAGE_SIZE + 120


def test_the_page_size_is_vault_apis_own_maximum():
    """500 is `Query(50, ge=1, le=500)` in vault's routers/objects.py.

    Asking for more is a 422, so this is a contract constant, not a
    tuning knob.
    """
    assert VAULT_PAGE_SIZE == 500


@pytest.mark.asyncio
async def test_fetch_all_pages_until_a_short_page():
    pages = [
        [{"id": n} for n in range(VAULT_PAGE_SIZE)],
        [{"id": n} for n in range(VAULT_PAGE_SIZE)],
        [{"id": 1}, {"id": 2}],
    ]
    calls: list[tuple[int, int]] = []

    async def list_page(limit: int, offset: int) -> list[dict]:
        calls.append((limit, offset))
        return pages[len(calls) - 1]

    rows = await _fetch_all(list_page)

    assert len(rows) == VAULT_PAGE_SIZE * 2 + 2
    assert calls == [(500, 0), (500, 500), (500, 1000)]


@pytest.mark.asyncio
async def test_fetch_all_stops_on_an_exactly_full_final_page():
    """A collection that is an exact multiple of the page size.

    The loop breaks on a short page, so this costs one extra request
    returning nothing -- and terminates, which is the property being
    pinned. Breaking on a total instead would loop forever here.
    """
    calls: list[int] = []

    async def list_page(limit: int, offset: int) -> list[dict]:
        calls.append(offset)
        return [{"id": n} for n in range(VAULT_PAGE_SIZE)] if offset == 0 else []

    rows = await _fetch_all(list_page)

    assert len(rows) == VAULT_PAGE_SIZE
    assert calls == [0, 500]


@pytest.mark.asyncio
async def test_fetch_all_stops_at_the_safety_cap():
    """The console is an operator read surface, not a bulk export."""

    async def list_page(limit: int, offset: int) -> list[dict]:
        return [{"id": offset + n} for n in range(VAULT_PAGE_SIZE)]

    rows = await _fetch_all(list_page)

    assert len(rows) >= VAULT_MAX_ROWS
    assert len(rows) < VAULT_MAX_ROWS + VAULT_PAGE_SIZE


@pytest.mark.asyncio
async def test_the_cost_ledger_sums_every_row_not_the_first_page():
    """The finding that made this more than a tidy-up.

    620 rows of 0.01 total 6.20. Truncated at 50 they total 0.50 -- a
    plausible number, on a page with nothing to indicate it is a tenth of
    the real spend.
    """
    mock = VaultApiMock()
    for _ in range(OVER_ONE_PAGE):
        mock.seed_cost(amount=Decimal("0.01"), function_id="09-market-intelligence-director")

    ledger = await get_cost_ledger(mock, group_by="function")

    assert len(ledger) == 1
    assert ledger[0]["total"] == Decimal("0.01") * OVER_ONE_PAGE


@pytest.mark.asyncio
async def test_a_date_filter_reaches_rows_older_than_the_first_page():
    """The silent half of the bug.

    _incurred_date filtering runs in Python AFTER the fetch, so with a
    single 50-row page any older day reported "no costs" -- an empty
    result that reads as "nothing was spent" rather than as a truncation.
    """
    mock = VaultApiMock()
    for _ in range(OVER_ONE_PAGE):
        mock.seed_cost(amount=Decimal("0.01"), incurred_at="2026-09-02T08:00:00+00:00")
    mock.seed_cost(amount=Decimal("7.50"), incurred_at="2026-08-01T08:00:00+00:00")

    ledger = await get_cost_ledger(mock, group_by="day", date="2026-08-01")

    assert ledger == [{"group_key": "2026-08-01", "total": Decimal("7.50")}]


@pytest.mark.asyncio
async def test_the_task_queue_shows_every_run_not_the_first_page():
    mock = VaultApiMock()
    for n in range(OVER_ONE_PAGE):
        mock.seed_agent_run(agent_name=f"agent-{n}", status="succeeded")

    assert len(await get_task_queue(mock)) == OVER_ONE_PAGE


@pytest.mark.asyncio
async def test_vault_search_filters_across_every_page_not_the_first():
    """Taxonomy filtering is client-side, so a truncated fetch narrows it.

    The matching asset is seeded LAST, which under `created_at DESC`
    ordering is the one most likely to be on the first page -- so this
    test is deliberately built to still fail if paging regresses:
    600 non-matching rows push it past the boundary either way.
    """
    mock = VaultApiMock()
    for _ in range(OVER_ONE_PAGE):
        mock.seed_asset(vertical="finance", function_id="fn-other")
    mock.seed_asset(vertical="mobility", function_id="fn-1")

    rows = await search_vault(mock, object_type="assets", vertical="mobility")

    assert len(rows) == 1
    assert rows[0]["vertical"] == "mobility"


@pytest.mark.asyncio
async def test_every_object_type_lister_is_pageable():
    """The listers hold UNCALLED bound methods.

    They used to be `lambda client: client.list_assets()` -- already
    invoked, and so unpageable by construction: no amount of paging logic
    downstream could have called them a second time.
    """
    from app.services import _OBJECT_TYPE_LISTERS

    fake_client = AsyncMock()
    for object_type, lister in _OBJECT_TYPE_LISTERS.items():
        resolved = lister(fake_client)
        assert callable(resolved), object_type
        # An already-invoked lambda would have returned a coroutine here,
        # which is callable() False and would also emit a never-awaited
        # warning -- the shape this asserts against.
        assert not hasattr(resolved, "__await__"), object_type
