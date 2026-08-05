"""analytics_ingest.utm — UTM campaign reconciliation (C-UTM).

analytics.utm_campaign_map + analytics.utm_quarantine are the SOLE UTM-to-
asset/campaign reconciliation mechanism (AC-10). reconcile_utm() validates
utm_campaign_raw against ^[a-z0-9-]+$:

  * null/empty                       -> quarantine, reason "missing_utm_campaign"
  * fails the regex                  -> quarantine, reason
                                         "invalid_utm_campaign_format: '<raw>'
                                         contains disallowed characters"
  * passes the regex but unmapped    -> quarantine, reason
                                         "unmatched_utm_campaign: no
                                         utm_campaign_map entry for slug '<raw>'"
  * passes the regex and maps        -> returns (vault_campaign_id, asset_id)

All quarantine writes use INSERT ... ON CONFLICT (source, day,
natural_row_id) DO NOTHING — never clobbers a hand-corrected quarantine
row (C-IDEMPOTENCY).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


async def _quarantine(
    pool: Any,
    source: str,
    day: date,
    natural_row_id: str,
    utm_campaign_raw: str | None,
    reason: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO analytics.utm_quarantine
                (source, day, natural_row_id, utm_campaign_raw, reason)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (source, day, natural_row_id) DO NOTHING
            """,
            source,
            day,
            natural_row_id,
            utm_campaign_raw,
            reason,
        )


async def reconcile_utm(
    pool: Any,
    source: str,
    day: date,
    natural_row_id: str,
    utm_campaign_raw: str | None,
) -> tuple[str, str] | None:
    """Reconcile one row's utm_campaign_raw value.

    Returns (vault_campaign_id, asset_id) on a successful match, or None
    after writing a quarantine row (with a non-null, human-readable
    reason) for any malformed/unmatched/missing value.
    """
    if not utm_campaign_raw:
        await _quarantine(
            pool, source, day, natural_row_id, utm_campaign_raw, "missing_utm_campaign"
        )
        return None

    if not _SLUG_RE.match(utm_campaign_raw):
        reason = f"invalid_utm_campaign_format: '{utm_campaign_raw}' contains disallowed characters"
        await _quarantine(pool, source, day, natural_row_id, utm_campaign_raw, reason)
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT vault_campaign_id, asset_id
            FROM analytics.utm_campaign_map
            WHERE utm_campaign_slug = $1
            """,
            utm_campaign_raw,
        )

    if row is None:
        reason = f"unmatched_utm_campaign: no utm_campaign_map entry for slug '{utm_campaign_raw}'"
        await _quarantine(pool, source, day, natural_row_id, utm_campaign_raw, reason)
        return None

    vault_campaign_id = row["vault_campaign_id"]
    asset_id = row["asset_id"]
    return (
        str(vault_campaign_id) if vault_campaign_id else None,
        str(asset_id) if asset_id else None,
    )
