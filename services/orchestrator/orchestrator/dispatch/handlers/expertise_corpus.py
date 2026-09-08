from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.config import functions_dir
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core
from ..completion import _complete_and_meter
from ..core import (
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
    logger,
)
from .source_lifecycle import LIFECYCLE_SIGNAL_LOOKBACK

# ---------------------------------------------------------------------
# Fn 113 -- Expertise Corpus Miner (Appendix D PR 8)
# ---------------------------------------------------------------------
#
# SCOPE CUT, DOCUMENTED (a privacy/consent decision, not just a missing
# vendor integration). prompt.md's approved input sources are fireflies_
# transcript, proposal, project_doc, linkedin_post_history, teams_message,
# email_thread, positioning_md -- every one of the first six is either (a)
# a live vendor API this repo has no provisioned credentials for (unlike
# Serper/Firecrawl in Appendix D PR 5c, which were a straightforward
# vendor-cost decision), or (b) REAL internal company/client
# conversations, whose extraction into a mined, potentially-committed
# corpus is a genuine confidentiality decision only Pieter can make --
# prompt.md's own rules underline this ("client-attended meetings: mine
# for language, pain and objections only... never extract a quotable
# client statement for public use"). This session's standing mandate to
# keep building autonomously does not extend to deciding, alone, what
# real meeting content is safe to mine. So this PR mines the one source
# that is already public, already committed, and already reviewed:
# docs/positioning.md. The mission's real nightly cadence (and the PR's
# own "corpus delta > 0 for 7 consecutive nights" bar) genuinely needs
# the deferred sources -- stated plainly here rather than faked by
# re-mining a static file that will correctly show delta=0 after its
# first run.

FUNCTION_ID_113 = "113-expertise-corpus-miner"
EXPERTISE_ATOM_BATCH_SIGNAL_TYPE = "expertise_atom_batch"
CORPUS_ZERO_DELTA_ALARM_DAYS = 7


def _positioning_md_path() -> Path:
    """PERMISSION_REGISTER_PATH's own pattern (functions/02-brand-steward-
    qa/permission_check.py's register_path()): env override wins (set by
    the orchestrator's Dockerfile for the deployed container, where docs/
    is staged as a single file), else the checkout-relative fallback."""
    override = os.environ.get("POSITIONING_MD_PATH", "").strip()
    if override:
        return Path(override)
    return functions_dir().parent / "docs" / "positioning.md"


def _normalize_atom_text(text: str) -> str:
    """Dedupe-by-meaning proxy (prompt.md: 'deduplicate against the
    existing corpus by meaning, not string match'). True semantic dedup
    needs embeddings infrastructure this repo does not have; this
    normalizes case/punctuation/whitespace so near-identical phrasing
    collapses, which is the cheap, honest subset of 'by meaning' this PR
    can actually deliver -- documented as a simplification, not silently
    presented as the full thing."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _existing_corpus_atom_texts(vault: VaultClientExt) -> set[str]:
    texts: set[str] = set()
    for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
        if row.get("signal_type") != EXPERTISE_ATOM_BATCH_SIGNAL_TYPE:
            continue
        for atom in (row.get("payload") or {}).get("atoms") or []:
            texts.add(_normalize_atom_text(str(atom.get("text", ""))))
    return texts


def expertise_corpus_mine_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 113 (prompt.md task, source docs/positioning.md only -- see
    module-section docstring above). No card: this function runs at Level
    4 and reports in the digest only when the delta is empty for
    CORPUS_ZERO_DELTA_ALARM_DAYS (logged, not a fabricated card kind --
    prompt.md names no card kind for this alarm)."""
    path = _positioning_md_path()
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_113
        )
        if not path.is_file():
            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("expertise-corpus-miner", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_113,
                status="succeeded",
                input_payload={"source_path": str(path)},
                output_payload={"status": "source_unavailable"},
            )
            db.set_result_ref(
                task_id,
                {
                    "status": "source_unavailable",
                    "agent_run_id": agent_run["id"],
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
            return

        existing = _existing_corpus_atom_texts(vault)
        source_text = path.read_text(encoding="utf-8")

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("expertise-corpus-miner", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_113,
            status="running",
            input_payload={"source_path": str(path), "existing_atom_count": len(existing)},
        )
        payload = {
            "source_type": "positioning_md",
            "source_text": source_text,
            "existing_atom_count": len(existing),
        }
        _validate_function_input(FUNCTION_ID_113, payload)

        with clients.build_gateway_client() as gateway:
            with emit_task_span(
                "expertise-corpus-mine",
                function_id=FUNCTION_ID_113,
                task_ref=task_id,
                model="claude-sonnet",
                run_id=str(envelope.campaign_id),
            ) as span:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=_read_prompt(FUNCTION_ID_113),
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    max_tokens=6144,
                )
                set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_113, output)

        new_atoms = []
        for atom in output["atoms"]:
            normalized = _normalize_atom_text(atom["text"])
            if normalized in existing:
                continue
            existing.add(normalized)
            new_atoms.append(atom)

        delta = {"new": len(new_atoms), "updated": 0, "retired": 0, "sources_scanned": 1}
        signal_id = None
        if new_atoms:
            batch = vault.create_signal(
                source=f"function-{FUNCTION_ID_113}",
                signal_type=EXPERTISE_ATOM_BATCH_SIGNAL_TYPE,
                payload={"atoms": new_atoms, "delta": delta, "source_path": str(path)},
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_113,
            )
            signal_id = batch["id"]
        else:
            log_event(
                logger,
                logging.INFO,
                "expertise_corpus_mine_zero_delta",
                source_path=str(path),
            )

        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={"atoms": new_atoms, "delta": delta},
            completed_at=_now_iso(),
        )

    result_ref = {
        "status": "mined",
        "new_atom_count": len(new_atoms),
        "sources_scanned": 1,
        "agent_run_id": agent_run["id"],
        "campaign_id": campaign_id,
    }
    if signal_id:
        result_ref["vault_signal_id"] = signal_id
    db.set_result_ref(task_id, result_ref)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


