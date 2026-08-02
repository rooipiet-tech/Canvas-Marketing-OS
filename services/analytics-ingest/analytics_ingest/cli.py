"""analytics_ingest.cli — CLI entrypoints (argparse, stdlib only).

Two subcommands:

  run     — targeted single/multi-source ingestion, for backfill/debugging.
            `--dry-run` needs no DATABASE_URL at all: it fetches each
            source's payload (dual-mode: live for Buffer if
            BUFFER_API_KEY is resolvable, else the bundled fixture) and
            prints simulated row counts without touching Postgres — this
            is what AC-28's literal verify command
            (`run --source buffer --day 2026-07-31 --dry-run`) exercises,
            with no Postgres available in that check.

  nightly — the FULL pipeline in order: ingest all 4 sources ->
            reconcile_utm for every newly-ingested row carrying a
            utm_campaign -> all 4 rollups -> export_fabric_day -> blob
            upload. This is the actual deployed nightly entrypoint —
            caj-analytics-nightly-ingest invokes `cli.py nightly`, never
            bare `run --source all` (see
            infra/modules/analytics/nightly-ingest-job.bicep and F1's
            root-cause note in .loop/plan.json). `--dry-run` still uses
            DATABASE_URL (any reachable — throwaway/test — Postgres is
            enough; no LIVE production DB is required) so every DB-
            touching stage genuinely executes and is provably exercised
            end-to-end (closing the F1 gap); the blob-upload stage is
            still attempted (so tests that monkeypatch
            analytics_ingest.blob_writer.upload can assert it was called
            with the validated export payload) but any failure there
            (e.g. no reachable Azure managed identity in a bare local
            run) is caught and reported as a simulated marker instead of
            crashing the whole pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from analytics_ingest import blob_writer, buffer_client, ga4_client, linkedin_client, search_console_client
from analytics_ingest import vault_client as vault_client_module
from analytics_ingest.db import close_pool, get_pool
from analytics_ingest.fabric_export import export_fabric_day
from analytics_ingest.ingest import (
    ingest_buffer_day,
    ingest_ga4_day,
    ingest_linkedin_day,
    ingest_search_console_day,
)
from analytics_ingest.rollup import (
    rollup_cost_per_accepted_asset,
    rollup_engagement_by_archetype,
    rollup_publishing_reliability,
    rollup_vault_utilisation,
)
from analytics_ingest.utm import reconcile_utm

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

SOURCES = ("buffer", "ga4", "search_console", "linkedin")

_INGEST_FUNCS = {
    "buffer": ingest_buffer_day,
    "ga4": ingest_ga4_day,
    "search_console": ingest_search_console_day,
    "linkedin": ingest_linkedin_day,
}

# natural_row_id field per source, used both by ingest.py's own inserts and
# by _reconcile_all below to keep the two in lockstep. search_console has
# no utm_campaign column at all (see migrations/0001_analytics_init.sql)
# and is excluded from reconciliation by construction.
_NATURAL_ROW_ID_FIELD = {
    "buffer": "post_id",
    "linkedin": "post_id",
    "ga4": "landing_page",
}


def _load_fixture(source: str, day: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / f"{source}_{day}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


async def _fetch_payload(source: str, day: str) -> list[dict[str, Any]]:
    """Dual-mode fetch: live where available (Buffer only, this session), fixture otherwise."""
    if source == "buffer":
        return await buffer_client.get_buffer_day(day)
    if source == "ga4":
        return ga4_client.get_ga4_day(day)
    if source == "search_console":
        return search_console_client.get_search_console_day(day)
    if source == "linkedin":
        return linkedin_client.get_linkedin_day(day)
    raise ValueError(f"unknown source: {source}")


async def _cmd_run(args: argparse.Namespace) -> int:
    sources = SOURCES if args.source == "all" else (args.source,)

    if args.dry_run:
        results = {}
        for source in sources:
            payload = await _fetch_payload(source, args.day)
            results[source] = {"rows_inserted": len(payload), "row_count": len(payload)}
        print(json.dumps(results))
        return 0

    pool = await get_pool()
    try:
        day = date.fromisoformat(args.day)
        results = {}
        for source in sources:
            payload = await _fetch_payload(source, args.day)
            ingest_func = _INGEST_FUNCS[source]
            results[source] = await ingest_func(pool, day, payload)
        print(json.dumps(results))
        return 0
    finally:
        await close_pool()


async def _reconcile_all(pool: Any, day: date, payloads: dict[str, list[dict[str, Any]]]) -> tuple[int, int]:
    """reconcile_utm() for every newly-ingested row carrying a utm_campaign value.

    Returns (reconciled, quarantined): reconciled is every row actually
    run through reconcile_utm (matched + quarantined); quarantined is only
    the rows reconcile_utm() itself quarantined (its return value is None
    on a quarantine outcome, non-None on a match — see utm.py).
    """
    reconciled = 0
    quarantined = 0
    for source, rows in payloads.items():
        natural_row_id_field = _NATURAL_ROW_ID_FIELD.get(source)
        if natural_row_id_field is None:
            continue
        for row in rows:
            natural_row_id = row.get(natural_row_id_field)
            if natural_row_id is None:
                continue
            utm_campaign = row.get("utm_campaign")
            outcome = await reconcile_utm(pool, source, day, natural_row_id, utm_campaign)
            reconciled += 1
            if outcome is None:
                quarantined += 1
    return reconciled, quarantined


async def run_nightly_pipeline(day_str: str, dry_run: bool = False) -> dict[str, Any]:
    """The full nightly pipeline, in order. Exposed as a plain function so
    tests can invoke it directly (per step 13/15's "or calls its
    underlying function directly" allowance) and monkeypatch
    analytics_ingest.blob_writer.upload."""
    day = date.fromisoformat(day_str)
    pool = await get_pool()
    try:
        # Stage 1: ingest all 4 sources.
        payloads = {source: await _fetch_payload(source, day_str) for source in SOURCES}
        ingested = {}
        for source, payload in payloads.items():
            ingested[source] = await _INGEST_FUNCS[source](pool, day, payload)

        # Stage 2: reconcile_utm for every newly-ingested row carrying a utm_campaign.
        reconciled, quarantined = await _reconcile_all(pool, day, payloads)

        # Stage 3: all 4 rollups.
        await rollup_engagement_by_archetype(pool, day)
        await rollup_publishing_reliability(pool, day)
        await rollup_cost_per_accepted_asset(pool, vault_client_module, day)
        await rollup_vault_utilisation(pool, vault_client_module, day)
        rolled_up = True

        # Stage 4: export_fabric_day.
        export_json = await export_fabric_day(pool, day)
        exported = True

        # Stage 5: blob upload — always attempted (so a monkeypatched
        # analytics_ingest.blob_writer.upload is genuinely called, dry-run
        # or not); a --dry-run run degrades a real failure (e.g. no
        # reachable Azure managed identity locally) to a simulated marker
        # rather than crashing the whole pipeline.
        if dry_run:
            try:
                uploaded = await blob_writer.upload(export_json, day_str)
            except Exception as exc:  # noqa: BLE001 - deliberate dry-run degrade
                uploaded = {"attempted": True, "error": str(exc)}
        else:
            uploaded = await blob_writer.upload(export_json, day_str)

        return {
            "ingested": ingested,
            "reconciled": reconciled,
            "quarantined": quarantined,
            "rolled_up": rolled_up,
            "exported": exported,
            "uploaded": uploaded,
        }
    finally:
        await close_pool()


async def _cmd_nightly(args: argparse.Namespace) -> int:
    summary = await run_nightly_pipeline(args.day, dry_run=args.dry_run)
    print(json.dumps(summary, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="analytics_ingest.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Targeted single/multi-source ingestion (backfill/debugging)."
    )
    run_parser.add_argument("--source", choices=(*SOURCES, "all"), required=True)
    run_parser.add_argument("--day", required=True)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=_cmd_run)

    nightly_parser = subparsers.add_parser(
        "nightly", help="The full nightly pipeline — the actual deployed entrypoint."
    )
    nightly_parser.add_argument("--day", required=True)
    nightly_parser.add_argument("--dry-run", action="store_true")
    nightly_parser.set_defaults(func=_cmd_nightly)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
