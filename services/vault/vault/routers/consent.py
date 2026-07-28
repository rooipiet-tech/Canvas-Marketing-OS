"""GET /consent/check — see contracts/vault-api.yaml checkConsent."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..consent import find_active_consent
from ..db import get_pool

router = APIRouter()


@router.get("/consent/check")
async def check_consent(
    data_subject_ref: str = Query(...),
    channel: str = Query(...),
    purpose: str = Query(...),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        consent_id = await find_active_consent(
            conn, data_subject_ref=data_subject_ref, channel=channel, purpose=purpose
        )
    return {
        "consented": consent_id is not None,
        "consent_register_id": str(consent_id) if consent_id else None,
    }
