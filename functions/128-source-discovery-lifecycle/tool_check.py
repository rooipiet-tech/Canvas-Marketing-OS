"""Deterministic mock + package-specific rubric checks for function 128.

Loaded dynamically by services/registry/eval_harness.py. Every golden task's
rubric here uses only the generic check kinds in services/registry/checks.py
(contains_all, not_contains, regex, json_valid, json_array_min_len,
json_array_max_len, json_field_equals) — this module supplies only
``mock_completion``, the deterministic stand-in for the model's reply.

CLIENT_DOMAIN is a fixture value standing in for a real
docs/permission-register.yaml entry — never a real client domain, per this
repo's no-client-names rule.
"""

from __future__ import annotations

import json

FUNCTION_ID = "128-source-discovery-lifecycle"

CLIENT_DOMAIN = "client-fixture-example.co.za"

CANDIDATE_POOL = {
    "competitors": [
        {
            "option_id": "A",
            "url": "https://www.itweb.co.za/rss/topic/data-analytics",
            "domain": "www.itweb.co.za",
            "distinctness_axis": "trade-press breadth",
            "rationale": "SA IT trade press topic feed, adjacent to the already-promoted "
            "competitor-discovery sources.",
            "provisional": False,
            "probe": {
                "reachable": True,
                "freshness_days": 1,
                "robots_allows": True,
                "on_allowlist": True,
                "duplicate_rate": 0.1,
                "forecast_yield_per_week": 4.0,
                "evidence_ref": "vault://probe-batch/2026-09-04/itweb-topic-feed",
            },
        },
        {
            "option_id": "B",
            "url": "https://www.moneyweb.co.za/news/companies-and-deals/feed/",
            "domain": "www.moneyweb.co.za",
            "distinctness_axis": "financial-press depth",
            "rationale": "Companies-and-deals feed, deeper on M&A/partnership signal "
            "than the general news feed already live.",
            "provisional": False,
            "probe": {
                "reachable": True,
                "freshness_days": 1,
                "robots_allows": True,
                "on_allowlist": True,
                "duplicate_rate": 0.05,
                "forecast_yield_per_week": 3.0,
                "evidence_ref": "vault://probe-batch/2026-09-04/moneyweb-companies-feed",
            },
        },
    ],
    "microsoft-fabric-power-bi": [
        {
            "option_id": "A",
            "url": "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=PowerBIBlog",
            "domain": "techcommunity.microsoft.com",
            "distinctness_axis": "product layer vs. partner layer",
            "rationale": "Power BI blog board, distinct from the Fabric board "
            "already promoted for fabric-ecosystem.",
            "provisional": False,
            "probe": {
                "reachable": True,
                "freshness_days": 2,
                "robots_allows": True,
                "on_allowlist": True,
                "duplicate_rate": 0.15,
                "forecast_yield_per_week": 2.0,
                "evidence_ref": "vault://probe-batch/2026-09-04/techcommunity-powerbi-board",
            },
        }
    ],
}


def _base_output(signal_class: str, card_kind: str = "source.promote") -> dict:
    candidates = CANDIDATE_POOL.get(signal_class, CANDIDATE_POOL["competitors"])
    return {
        "card_kind": card_kind,
        "signal_class": signal_class,
        "candidates": candidates,
        "recommended_option_id": candidates[0]["option_id"],
    }


def mock_completion(task: dict, prompt_text: str) -> str:
    task_input = task.get("input", {})
    signal_class = task_input.get("signal_class", "competitors")
    mode = task_input.get("mode", "promote")

    if mode == "client-domain-probe":
        # Exercises hard rule 2: a client domain must never appear as a
        # candidate, even when it was present in the raw discovery query
        # results the input simulates.
        output = _base_output(signal_class)
        assert CLIENT_DOMAIN not in json.dumps(output)
        return json.dumps(output)

    if mode == "retire":
        output = _base_output(signal_class, card_kind="source.retire")
        output["retiring_source_url"] = task_input.get(
            "underperforming_source_url", "https://example-retiring-source.test/feed"
        )
        return json.dumps(output)

    if mode == "provisional":
        output = _base_output(signal_class)
        output["candidates"] = [dict(c) for c in output["candidates"]]
        output["candidates"][0]["provisional"] = True
        if len(output["candidates"]) > 1:
            output["candidates"][1]["provisional"] = False
        return json.dumps(output)

    return json.dumps(_base_output(signal_class))
