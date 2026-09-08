from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

from orchestrator.config import functions_dir
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..core import CONTENT_PILLARS, _agent_name, _campaign_name, _now_iso, logger
from ..lineage import resolve_lineage_result
from .scanner import _ancestor_is_quiet, _complete_quiet_scan_noop

# ---------------------------------------------------------------------
# score-signals (F-NO-SCORING)
# ---------------------------------------------------------------------
#
# "Score what matters" is step 2 of the pipeline the README advertises,
# and it did not exist. score-signals fell through to
# legacy_task_pass_through, and opportunity_cards -- a table in the frozen
# vault schema, routed by the Vault API, indexed by campaign -- had NO
# WRITER anywhere in the codebase. draft-brief rendered every signal in
# whatever order the model happened to emit them.
#
# DELIBERATELY DETERMINISTIC, like draft-brief and for the same reason:
# ranking evidence is not a language problem, and inventing an unreviewed
# scoring prompt would put unapproved policy in the daily path (see
# function 48's own header for how that is regarded here). function_id is
# signal.score, mirroring brief.compose -- a real function id with no
# numbered prompt package behind it, because there is no model call.
#
# THE SCORE IS DELIBERATELY SIMPLE AND SAYS SO. It is function 09's own
# `confidence`, mapped to a number. That is the only per-signal quality
# judgement anything in the system currently produces, and it is already
# governed by prompt rules the evals check (never round thin evidence up).
# Anything richer -- pillar weighting, vertical priority, recency decay,
# corroboration across sources -- is business policy that nobody has
# written down, and inventing a weighting here would bury an unreviewed
# opinion in a number that later reads as fact. When that policy exists,
# it belongs in reviewable YAML beside the scan profiles, and this
# function is where it plugs in.

FUNCTION_ID_SIGNAL_SCORE = "signal.score"

# Deliberately coarse. These are the FALLBACK weights, used when no policy
# file is readable -- functions/_shared/scoring-policy.yaml carries the
# reviewable copy, and _load_scoring_policy() prefers it. They are kept
# here, and kept identical to that file's shipped values, so scoring
# degrades to the behaviour it had before the policy file existed rather
# than to nothing. _rank_cards (the fan-out path) reads CONFIDENCE_SCORES
# directly, which is why it stays a plain module-level dict.
CONFIDENCE_SCORES = {"high": 0.8, "medium": 0.5, "low": 0.25}
UNKNOWN_CONFIDENCE_SCORE = 0.1

SCORING_POLICY_PATH = ("_shared", "scoring-policy.yaml")

# A brief with no signals is a failure to report, not a report -- so a
# minimum_score that would empty the batch still keeps this many.
MIN_SELECTED_SIGNALS = 1


@dataclass(frozen=True)
class ScoringPolicy:
    """What "matters" means, loaded from scoring-policy.yaml.

    Its defaults are exactly the hardcoded rule score-signals shipped
    with, so an absent or empty policy file changes nothing.
    """

    confidence_weights: dict[str, float] = field(
        default_factory=lambda: dict(CONFIDENCE_SCORES)
    )
    unknown_confidence: float = UNKNOWN_CONFIDENCE_SCORE
    pillar_weights: dict[str, float] = field(default_factory=dict)
    top_n: int | None = None
    minimum_score: float | None = None

    @property
    def filters(self) -> bool:
        """Whether this policy can hold a signal back from the brief."""
        return self.top_n is not None or self.minimum_score is not None


def _load_scoring_policy() -> ScoringPolicy:
    """functions/_shared/scoring-policy.yaml, resolved through
    functions_dir() at call time for the same reason
    _load_scan_profiles() does (see config.functions_dir()).

    REFUSES a policy it cannot honour, rather than degrading to the
    default: a typo'd pillar name or an out-of-range weight is somebody
    trying to change what the daily loop considers important and failing
    silently, which is worse than not being able to change it at all.

    A MISSING file is the one case that degrades quietly, to the shipped
    defaults -- an orchestrator image built before this file existed must
    keep scoring rather than dead-letter every daily run.
    """
    path = functions_dir().joinpath(*SCORING_POLICY_PATH)
    if not path.exists():
        log_event(
            logger,
            logging.WARNING,
            "scoring_policy_absent",
            path=str(path),
            detail="scoring with built-in defaults",
        )
        return ScoringPolicy()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    policy_file = f"functions/{'/'.join(SCORING_POLICY_PATH)}"

    weights = raw.get("confidence_weights") or dict(CONFIDENCE_SCORES)
    unknown = set(weights) - set(CONFIDENCE_SCORES)
    if unknown:
        raise DispatchError(
            f"{policy_file}: confidence_weights names {sorted(unknown)}, which is not "
            f"function 09's confidence enum {sorted(CONFIDENCE_SCORES)}"
        )

    pillar_weights = raw.get("pillar_weights") or {}
    unknown_pillars = set(pillar_weights) - set(CONTENT_PILLARS)
    if unknown_pillars:
        raise DispatchError(
            f"{policy_file}: pillar_weights names {sorted(unknown_pillars)}, which is not "
            f"function 09's pillar enum -- a typo here silently stops weighting the "
            "pillar it was meant to weight"
        )

    selection = raw.get("selection") or {}
    top_n = selection.get("top_n")
    if top_n is not None and int(top_n) < 1:
        raise DispatchError(f"{policy_file}: selection.top_n must be at least 1, got {top_n}")

    return ScoringPolicy(
        confidence_weights={key: float(value) for key, value in weights.items()},
        unknown_confidence=float(raw.get("unknown_confidence", UNKNOWN_CONFIDENCE_SCORE)),
        pillar_weights={key: float(value) for key, value in pillar_weights.items()},
        top_n=None if top_n is None else int(top_n),
        minimum_score=(
            None
            if selection.get("minimum_score") is None
            else float(selection["minimum_score"])
        ),
    )


def _score_signal(signal: dict[str, Any], policy: ScoringPolicy | None = None) -> float:
    """One signal's score: its confidence weight, multiplied by its
    pillar's weight, clamped to 1.0.

    `policy` is optional so a caller that only needs the shipped rule --
    and every caller that predates the policy file -- keeps working
    unchanged; pass one to score under a loaded policy.
    """
    policy = policy or ScoringPolicy()
    base = policy.confidence_weights.get(
        str(signal.get("confidence", "")), policy.unknown_confidence
    )
    weight = policy.pillar_weights.get(str(signal.get("pillar", "")), 1.0)
    return round(min(base * weight, 1.0), 4)


def _apply_selection(
    ranked: list[dict[str, Any]], policy: ScoringPolicy
) -> list[dict[str, Any]]:
    """Mark which ranked signals reach the brief.

    Marks rather than drops: every scored signal still gets an
    opportunity_card, so the Vault keeps the whole scan regardless of what
    the brief shows. A policy with no cut selects everything, which is the
    shipped state.
    """
    floor = policy.minimum_score
    keep = len(ranked) if policy.top_n is None else min(policy.top_n, len(ranked))
    for index, item in enumerate(ranked):
        above_floor = floor is None or item["score"] >= floor
        item["selected"] = index < keep and above_floor
    if not any(item["selected"] for item in ranked):
        # A floor that empties the batch would produce a signal-less brief.
        # Keep the best-scored signal and let the brief say the rest were
        # held back -- reporting a thin day beats reporting nothing.
        for item in ranked[:MIN_SELECTED_SIGNALS]:
            item["selected"] = True
    return ranked


def _rank_signals(
    signal_output: dict[str, Any], policy: ScoringPolicy | None = None
) -> list[dict[str, Any]]:
    """Signals highest-score first, ties broken by the order function 09
    emitted them -- a stable sort, so the same batch always ranks the same
    way and a reviewer comparing two runs sees real change rather than
    sort noise."""
    policy = policy or ScoringPolicy()
    ranked = [
        {
            "headline": str(signal.get("headline", "")),
            "so_what": str(signal.get("so_what", "")),
            "source_url": str(signal.get("source_url", "")),
            "pillar": str(signal.get("pillar", "")),
            "confidence": str(signal.get("confidence", "")),
            "score": _score_signal(signal, policy),
            "position": index,
        }
        for index, signal in enumerate(signal_output.get("signals") or [])
    ]
    ranked.sort(key=lambda item: (-item["score"], item["position"]))
    return _apply_selection(ranked, policy)


def score_signals_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("score-signals: no ancestor task carries a result_ref to score")
    _ancestor_task, ancestor_ref = lineage
    if _ancestor_is_quiet(ancestor_ref):
        _complete_quiet_scan_noop(
            task_id, db, stage="score-signals", reason="ingest reported a quiet scan"
        )
        return
    signal_id = ancestor_ref.get("vault_signal_id")
    if not signal_id:
        raise DispatchError("score-signals: ancestor result_ref carries no vault_signal_id")

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        signal = vault.get_signal(signal_id)
        signal_output = signal.get("payload", {})
        topic = ancestor_ref.get("topic") or signal_output.get("topic", "morning brief")
        policy = _load_scoring_policy()
        ranked = _rank_signals(signal_output, policy)
        if not ranked:
            # _rank_signals maps every signal to a ranked item and
            # _apply_selection only sets a `selected` flag, so an empty
            # `ranked` means an empty batch -- not a policy that filtered
            # everything out. Defence in depth behind the marker above,
            # for a batch written before the marker existed.
            _complete_quiet_scan_noop(
                task_id,
                db,
                stage="score-signals",
                reason=f"signal batch {signal_id} carries no signals to score",
            )
            return
        held_back = [item for item in ranked if not item["selected"]]
        log_event(
            logger,
            logging.INFO,
            "signals_scored",
            task_id=task_id,
            scored=len(ranked),
            selected=len(ranked) - len(held_back),
            held_back=len(held_back),
            # Which policy decided, so a brief that looks thin can be
            # traced to the cut that made it thin rather than to the scan.
            top_n=policy.top_n,
            minimum_score=policy.minimum_score,
            weighted_pillars=sorted(policy.pillar_weights),
        )

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("signal-scorer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
            status="running",
            input_payload={
                "vault_signal_id": signal_id,
                "signal_count": len(ranked),
                "selected_count": len(ranked) - len(held_back),
                "policy": {
                    "top_n": policy.top_n,
                    "minimum_score": policy.minimum_score,
                    "pillar_weights": policy.pillar_weights,
                },
            },
        )

        for item in ranked:
            card = vault.create_opportunity_card(
                signal_id=signal_id,
                title=item["headline"],
                score=item["score"],
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_SIGNAL_SCORE,
                # The evidence behind the number. Without these a card is
                # a headline and a score: unreadable by a person (no
                # source to check) and unusable by code (no pillar to
                # group by), which is why nothing read this table.
                pillar=item["pillar"],
                so_what=item["so_what"],
                source_url=item["source_url"],
                confidence=item["confidence"],
            )
            item["opportunity_card_id"] = card["id"]

        card_ids = [item["opportunity_card_id"] for item in ranked]
        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={"opportunity_card_ids": card_ids},
            completed_at=_now_iso(),
        )

    with emit_task_span(
        "score-signals",
        function_id=FUNCTION_ID_SIGNAL_SCORE,
        task_ref=task_id,
        model="none",
        cost=0.0,
        run_id=str(envelope.campaign_id),
    ):
        pass  # deterministic scoring only -- no gateway call, no cost

    db.set_result_ref(
        task_id,
        {
            # Superset of what ingest published. score-signals now carries a
            # result_ref, so it -- not ingest -- is what
            # resolve_lineage_result hands draft-brief; these keys must
            # therefore keep answering draft-brief's own questions.
            "vault_signal_id": signal_id,
            "topic": topic,
            "campaign_id": campaign_id,
            "agent_run_id": agent_run["id"],
            "opportunity_card_ids": card_ids,
            "ranking": ranked,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


