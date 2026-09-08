from __future__ import annotations

import csv
import io
import logging
import os
from typing import Any

from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope

from .. import clients
from ..core import FUNCTION_ID_45, logger
from .drafting import _build_carousel_payload, _draft_social_post_handler

CAROUSEL_BULK_CSV_MARKER = "--- canva_bulk_create_csv ---"


def _render_carousel(output: dict[str, Any]) -> str:
    lines = ["[CAROUSEL]"]
    for slide in output.get("slides", []):
        lines.append(
            f"Slide {slide.get('slide_number')}: {slide.get('headline')} — "
            f"{slide.get('subhead')}"
        )
    lines.append("")
    lines.append(f"CTA: {output.get('cta_url', '')}")
    lines.append("")
    lines.append(CAROUSEL_BULK_CSV_MARKER)
    lines.append(output.get("canva_bulk_create_csv", ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------
# A3 (2 Sep 2026) -- the carousel's Canva manifest finally reaches Canva
# ---------------------------------------------------------------------
# mcp-canva shipped with three template-locked tools, its own managed
# identity, Key Vault role, ACR role, Container App and a smoke job, and
# NOTHING in this system ever called it. Function 45 produced a Canva Bulk
# Create CSV manifest, validated its own shape locally, and the manifest
# stopped there -- someone had to paste it into Canva by hand.
#
# WHY IT RUNS HERE AND NOT AS ITS OWN LOOP STAGE. Generating the deck is
# part of producing the carousel asset, not part of publishing it.
# Function 45's own tools.yaml draws that line: drafting is auto-approved
# under `draft.social_post`, and "when the resulting carousel is later
# scheduled for publication, that downstream step runs under a different,
# publish-class identifier". A Canva design lives in Canva's workspace and
# reaches no audience, so it needs no separate gate-check -- the publish
# gate still stands between it and anyone seeing it.
#
# It runs AFTER _draft_social_post_handler returns, so the draft, its
# Vault asset and its result_ref all exist first, and it is best-effort:
# a Canva outage must not dead-letter a perfectly good carousel draft.
# Thursday's QA reviews the slide copy, not the deck, and the manifest is
# still in the draft text for manual bulk-create either way. A failure
# here is a logged warning, never a task failure.
#
# POPIA s72: the carousel carries client-free proof points by contract
# (function 45's schema, and Brand Steward's clearance check upstream of
# publication), so no personal information crosses the border on this
# path. That is a property of the content, not of this code, which is why
# it is asserted in the function's schema rather than re-checked here.

CANVA_MANIFEST_TEMPLATE_COLUMN = "brand_template_id"


def canva_dry_run() -> bool:
    """Default TRUE, mirroring PUBLISHER_DRY_RUN's convention exactly.

    Set CMOS_CANVA_DRY_RUN=false to let the carousel handler actually call
    mcp-canva. It stays true until canva-refresh-token is populated,
    because until then mcp-canva cannot reach Canva anyway -- so the honest
    default is the one that says so in the logs rather than the one that
    fails a call every Wednesday.
    """
    return os.environ.get("CMOS_CANVA_DRY_RUN", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


def _parse_canva_manifest(csv_text: str) -> tuple[str | None, list[dict[str, str]]]:
    """Function 45's CSV manifest -> (brand template id, one dict per row).

    The template id is a COLUMN in the manifest because a flat CSV has
    nowhere else to put it, but it is a job-level concern for Canva's
    autofill API (one job, one template, one design). It is lifted out
    here rather than at the MCP boundary so a manifest whose rows disagree
    about the template is caught before anything is submitted -- that is a
    malformed deck, not two decks.

    Returns (None, []) for anything unparseable. The caller treats that as
    "nothing to generate", which is the same outcome as a manifest that
    was never produced.
    """
    text = (csv_text or "").strip()
    if not text:
        return None, []
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        return None, []
    if not rows:
        return None, []

    template_ids = {
        (row.get(CANVA_MANIFEST_TEMPLATE_COLUMN) or "").strip()
        for row in rows
    }
    template_ids.discard("")
    if len(template_ids) != 1:
        return None, []

    cleaned = [
        {key: (value or "").strip() for key, value in row.items() if key}
        for row in rows
    ]
    return template_ids.pop(), cleaned


def _generate_carousel_designs(task_id: str, output: dict[str, Any], db: Any) -> None:
    """Hand function 45's manifest to mcp-canva's bulk_create_from_csv."""
    template_id, rows = _parse_canva_manifest(output.get("canva_bulk_create_csv", ""))
    if not template_id or not rows:
        log_event(
            logger,
            logging.INFO,
            "carousel_canva_manifest_not_generatable",
            task_id=task_id,
            row_count=len(rows),
            has_template_id=bool(template_id),
        )
        return

    if canva_dry_run():
        log_event(
            logger,
            logging.INFO,
            "carousel_canva_dry_run",
            task_id=task_id,
            template_id=template_id,
            slide_count=len(rows),
        )
        return

    with clients.build_mcp_canva_client() as canva:
        result = canva.call_tool(
            "bulk_create_from_csv",
            {"template_id": template_id, "rows": rows},
        )

    log_event(
        logger,
        logging.INFO,
        "carousel_canva_designs_requested",
        task_id=task_id,
        template_id=template_id,
        slide_count=len(rows),
        source=result.get("source"),
    )


def draft_carousel_post_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-carousel-post",
        function_id=FUNCTION_ID_45,
        prompt_dir="45-carousel-post-writer",
        agent_name="carousel-post-writer",
        asset_type="carousel_post",
        render_draft_text=_render_carousel,
        build_payload=_build_carousel_payload,
        max_tokens=2560,
        on_draft_complete=_generate_carousel_designs,
    )

