"""Publisher runtime configuration, read from the process environment."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from governance_lib.constants import AGENT_NAME_LOOP_PROOF  # noqa: F401 -- re-exported below

# The ONE algorithm Publisher accepts. See app/verifier.py for why RS256
# and not EdDSA (this Key Vault SKU has no Ed25519 key type at all).
DEFAULT_ALLOWED_ALGORITHMS = ("RS256",)

# ---------------------------------------------------------------------
# Buffer / dry-run / proof-circuit constants (plan step 14; AC-07/08/09/30;
# DE-3; GOAL "behind a dry-run flag" / "free-tier cap 10 scheduled posts").
# ---------------------------------------------------------------------


def publisher_dry_run() -> bool:
    """Default dry-run (true). Set PUBLISHER_DRY_RUN=false to flip to live
    mode -- but see vault_lookup.py: a request whose asset_id resolves to
    a loop-proof-circuit-tagged asset ALWAYS stays dry-run regardless of
    this flag's value (AC-08(e), AC-30)."""
    return os.environ.get("PUBLISHER_DRY_RUN", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


# Buffer's free-tier plan caps queued posts at 10 (DE-3: an ASSUMPTION
# sourced from the GOAL text, not independently verifiable from any file
# in this repo -- see .loop/domain.md DE-3). Enforced as a live list_queue
# count check (app/buffer_client.py), not a static config value alone.
#
# DECISION, 2 Sep 2026 (backlog B1, Pieter): KEEP THE FREE TIER. The cap
# is accepted as a throttle, not bought out.
#
# ARITHMETIC CORRECTED THE SAME DAY, BEFORE THIS SHIPPED. The first
# version of this note said "four Buffer posts per WEEK", read off
# weekly-content-loop.yaml's name and its then-current header ("Fired
# Monday 07:00 SAST"). That header was stale. The authoritative source is
# infra/modules/scheduling/weekly-planning-trigger.bicep, which has run
# `frequency: 'Day', interval: 1` since 6 Aug 2026 and was confirmed as
# the standing cadence on 17 Aug. The loop id and its Monday..Friday task
# prefixes are DEPENDENCY-CHAIN names, not a schedule: one heartbeat
# decomposes the whole graph, so a daily fire is one COMPLETE content
# cycle per day.
#
# Exactly the failure CLAUDE.md's own convention names -- an external
# identifier, or here a cadence, is a hypothesis until the authoritative
# source says it. A comment is not that source.
#
# THE REAL NUMBERS:
#
#   Up to FOUR Buffer posts per cycle -- friday-schedule-social-buffer-
#   {insight-story,ghostwrite,carousel,repurpose}. friday-publish-
#   newsletter goes out on the ESP path and never queues. The cap is
#   checked against ONE channel, LinkedIn, in routers/publish.py.
#
#   One cycle per DAY, seven days a week. So up to 4/day arriving against
#   10 held -- the cap can reject inside three days of a stalled queue,
#   not two and a half weeks.
#
# WHY THE DECISION STILL STANDS ON THE CORRECTED NUMBERS. At 4/day the
# cap is not a ceiling on cadence, it is a ceiling on BACKLOG: it binds
# only if Buffer publishes fewer than 4 posts a day from this channel's
# posting schedule. If the schedule has 4+ daily slots the queue never
# accumulates and the tier is irrelevant; if it has fewer, the queue grows
# without bound and a paid tier only buys days before the same wall.
# Either way the fix is the posting schedule, not the plan.
#
# WHAT NOBODY HAS CHECKED: how many daily slots the LinkedIn channel's
# Buffer posting schedule actually has. Nothing in this repo records it.
# That is the number this decision really turns on, and the alert below is
# how we find it out.
#
# Also true and worth stating plainly: PUBLISHER_DRY_RUN defaults to true
# and is unset in infra, so no post has ever entered the real queue. This
# cap has never bound in production and cannot until that gap closes.
#
# Revisit if the per-cycle count goes above four, or if anyone verifies
# Buffer's actual free-tier number and it is not 10.
#
# COSTED COMPARISON, 7 Sep 2026 (docs/architecture/09-technical-debt.md
# TD-36; DE-3's "not independently verifiable" caveat above, closed).
# The decision above (KEEP THE FREE TIER) already stands and this does not
# reopen it -- it fills in the numbers that decision was missing, in case
# it is ever revisited. Verified against third-party Buffer pricing
# trackers, not buffer.com itself (unreachable from this environment).
#
#   Paid tier: Buffer's Essentials plan is ~$5/mo per channel (annual) or
#   $6/mo (monthly), unlimited scheduled posts -- the cap goes away
#   entirely. ~$15-18/mo for the 3 channels already wired here
#   (LinkedIn/Facebook/X). The free plan's own stated limits are 3
#   channels (this org is already at that ceiling) and 10 scheduled posts
#   per channel, concurrent -- confirms the 10 above is a real number, not
#   just DE-3's assumption.
#
#   Fewer posts per cycle: would mean dropping one of the four
#   friday-schedule-social-buffer-* tasks in weekly-content-loop.yaml.
#   Only buys days, not a fix, per the arithmetic above -- and costs
#   whichever draft loses its weekly social surface.
#
#   Accept as throttle: nothing left to build. The refusal path
#   (buffer_queue_cap_exceeded) and the queue-depth warning below both
#   already ship.
#
# Which of the three to take is Pieter's call, not this comment's --
# already made once (see above); this only makes the next revisit informed.
BUFFER_FREE_TIER_QUEUE_CAP = 10

# Warn with one full cycle of headroom left. Six, not eight: at up to four
# posts per DAILY cycle, a queue at 8 is under twelve hours from
# rejections, which is not enough warning to be worth having. Six leaves
# one whole cycle to notice and act.
#
# This was 8 in the first version of this change, chosen against the wrong
# cadence (see the correction above). The intent has not moved -- warn
# while the queue can still be drained -- only the number that satisfies
# it.
#
# Deliberately BELOW the cap either way: an alert that fires AT the cap
# fires only once posts are already being refused, which is the state it
# exists to prevent.
BUFFER_QUEUE_DEPTH_WARN_AT = 6

# Buffer channel/org ids: policy data (policy/buffer-channels.yaml), not a
# literal here -- that file maps ALL 3 known channel ids (LinkedIn/
# Facebook/X) + org together so the GOAL-prose transposition error
# (.loop/spec.json's v3 amendment: the GOAL text mistakenly used the X
# channel id as the LinkedIn id) can never recur silently. See that file's
# own header for the full rationale; only linkedin_channel_id and org_id
# are read here today.
BUFFER_CHANNELS_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "policy" / "buffer-channels.yaml"
)

_buffer_channels_policy = yaml.safe_load(
    BUFFER_CHANNELS_POLICY_PATH.read_text(encoding="utf-8")
)

BUFFER_LINKEDIN_CHANNEL_ID = _buffer_channels_policy["channels"]["linkedin_channel_id"]
BUFFER_ORG_ID = _buffer_channels_policy["org_id"]

# AGENT_NAME_LOOP_PROOF is imported from governance_lib.constants above
# (TD-08) and re-exported here so existing `from app.config import
# AGENT_NAME_LOOP_PROOF` call sites (app/routers/publish.py,
# tests/test_proof_circuit_dry_run_override.py) need no change.
# orchestrator/dispatch.py imports the identical constant, so the two can
# no longer drift the way PV2-03's residual-risk mitigation originally
# guarded against with a cross-service equality test (see
# tests/test_agent_name_constant_matches_orchestrator.py).


def database_url() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "neither TEST_DATABASE_URL nor DATABASE_URL is set — Publisher has "
            "no durable store for the jti replay ledger or publish_attempts"
        )
    return dsn


def token_issuer() -> str:
    return os.environ.get("GATE_TOKEN_ISSUER", "cmos-gatekeeper")


def token_audience() -> str:
    return os.environ.get("GATE_TOKEN_AUDIENCE", "cmos-publisher")


def allowed_algorithms() -> tuple[str, ...]:
    raw = os.environ.get("GATE_TOKEN_ALGORITHMS")
    if not raw:
        return DEFAULT_ALLOWED_ALGORITHMS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def gate_token_public_key_pem() -> str:
    """Public verification key.

    Preferred source is GATE_TOKEN_PUBLIC_KEY_PEM (threaded in as a
    Container Apps secret). Falling back to Key Vault is a lazy import so
    the verifier stays importable without the Azure SDK installed.
    """
    pem = os.environ.get("GATE_TOKEN_PUBLIC_KEY_PEM")
    if pem:
        # Container Apps secret values are base64-encoded on the way in
        # (defends against the "$$" -> "$" collapse); PEM has no "$", but
        # base64 is the established convention for anything threaded
        # through a Container Apps secret, so accept either form.
        if "-----BEGIN" not in pem:
            import base64

            pem = base64.b64decode(pem).decode("utf-8")
        return pem

    vault_url = os.environ.get("KEY_VAULT_URL")
    key_name = os.environ.get("GATE_SIGNING_KEY_NAME", "gate-token-signing-key")
    if not vault_url:
        raise RuntimeError(
            "no gate-token verification key: set GATE_TOKEN_PUBLIC_KEY_PEM or KEY_VAULT_URL"
        )
    return _public_key_pem_from_key_vault(vault_url, key_name)


def _public_key_pem_from_key_vault(vault_url: str, key_name: str) -> str:
    """Fetch the PUBLIC half of the signing key.

    Publisher's managed identity holds a verify/get-capable role only — it
    can read the public key and call verify, and cannot sign (AC-20).
    """
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.keys import KeyClient
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    client = KeyClient(vault_url=vault_url, credential=DefaultAzureCredential())
    jwk = client.get_key(key_name).key
    public_key = rsa.RSAPublicNumbers(
        e=int.from_bytes(jwk.e, "big"), n=int.from_bytes(jwk.n, "big")
    ).public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
