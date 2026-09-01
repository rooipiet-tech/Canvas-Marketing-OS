"""In-memory fakes for orchestrator/dispatch.py's 4 client dependencies
(gateway/vault/gatekeeper/mcp-web), shared by test_worker_loop.py and
test_dispatch.py.

These are NOT httpx.MockTransport doubles (dispatch.py's handlers build
their own clients via the build_*_client() module-level factory functions
specifically so a test can monkeypatch exactly one of those per handler
under test — see dispatch.py's own docstring) — each Fake* class below
implements the same PUBLIC method surface as its real counterpart
directly, in-memory, so a test never needs a live model-gateway/Vault/
Gatekeeper/mcp-web to prove the ORCHESTRATION wiring (dispatch table,
lineage resolution, result_ref plumbing, span emission) is correct.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any


def _fake_cta_url(user_content: str) -> str:
    """Every drafting function's output schema pins cta_url to
    ^https://www\\.canvasintelligence\\.com/ and every one of their prompts
    requires utm parameters on it. Built from the payload's own `campaign`
    so a test can prove the whole week's assets share one attribution tag
    rather than six invented ones."""
    campaign = json.loads(user_content)["campaign"]
    return (
        "https://www.canvasintelligence.com/insights"
        f"?utm_source=linkedin&utm_medium=social&utm_campaign={campaign}"
    )


class FakeGatewayClient:
    """Detects which function is being invoked from a keyword in the
    system prompt and returns a schema-valid canned CompletionResponse —
    good enough to exercise dispatch.py's parsing/validation path without
    a real model call."""

    def __enter__(self) -> "FakeGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(
        self, *, model: str, system_prompt: str, user_content: str, agent_run_id: str, **_kw: Any
    ) -> dict[str, Any]:
        # Order matters: function 42's own prompt.md mentions "Brand
        # Steward QA function (function 02)" in its hard rules, so the
        # more specific titles must be checked BEFORE the "Brand Steward"
        # substring or a LinkedIn-post request would be misdetected as a
        # QA request.
        if "Market Intelligence Director" in system_prompt:
            content = json.dumps(
                {
                    "topic": (
                        "Microsoft Fabric adoption and multi-entity finance consolidation "
                        "in South African enterprises"
                    ),
                    "horizon_days": 30,
                    "summary": (
                        "Fabric adoption accelerated across SA enterprises this window, with "
                        "consolidation tooling drawing renewed CFO attention."
                    ),
                    "signals": [
                        {
                            "headline": "Microsoft ships new Fabric capacity tooling",
                            "so_what": "Lowers the cost floor for mid-market Fabric adoption",
                            "source_url": "https://learn.microsoft.com/en-us/fabric/get-started/whats-new",
                            "pillar": "Fabric-native",
                            "confidence": "high",
                        },
                        {
                            "headline": "SA business press covers finance data consolidation trend",
                            "so_what": "Confirms CFO appetite for one governed source of truth",
                            "source_url": "https://www.moneyweb.co.za/feed/",
                            "pillar": "Consolidation at scale",
                            "confidence": "medium",
                        },
                        {
                            "headline": "SA tech press covers analytics platform spend",
                            "so_what": "Signals budget available for productised platforms",
                            "source_url": "https://businesstech.co.za/news/feed/",
                            "pillar": "Productised speed",
                            "confidence": "low",
                        },
                    ],
                }
            )
        elif "LinkedIn Post Writer" in system_prompt:
            body = (
                "Finance teams keep hearing a different number for the same question. "
                "Consolidation at scale: one recent engagement consolidated 40+ business "
                "units across 14+ ERP systems into a single governed Azure lakehouse. "
                "This is Consolidation at scale in practice. "
                "Read more: https://www.canvasintelligence.com/insights?"
                "utm_source=linkedin&utm_medium=social&utm_campaign=loop-proof\n"
                "Your Data. Delivered."
            )
            content = json.dumps(
                {
                    "post": body,
                    "pillar": "Consolidation at scale",
                    "cta_url": (
                        "https://www.canvasintelligence.com/insights?utm_source=linkedin"
                        "&utm_medium=social&utm_campaign=loop-proof"
                    ),
                }
            )
        elif "Competitive Response Strategist" in system_prompt:
            # Function 25. Absent until the scanners' dead tail was wired
            # up -- nothing had ever called this function, so nothing had
            # ever needed a branch. Echoes the supplied cards back as a
            # ranked plan, in function 25's own output shape, so a test
            # can prove the cards that went in are the ones planned over.
            cards = json.loads(user_content)["cards"]
            content = json.dumps(
                {
                    "summary": (
                        "Competitors moved on two fronts this week; the response below "
                        "reasserts the proof points that already answer them."
                    ),
                    "response_plan": [
                        {
                            **card,
                            "taxonomy": card.get("taxonomy") or "proof-reassertion",
                            "evidence_grade": card.get("evidence_grade") or "moderate",
                            "confidence": "medium",
                            "severity": "high" if index == 0 else "medium",
                            "playbook_template": "reassert-differentiation",
                        }
                        for index, card in enumerate(cards)
                    ],
                }
            )
        elif "Fact-Check Verdict" in system_prompt:
            # Function 48. Absent until an end-to-end test walked a draft
            # through both Thursday gates: the call fell to the `{}`
            # default and the output validation added with List D
            # correctly rejected it. Placed ABOVE the Research Brief
            # branch on purpose -- 48's prompt describes the "{claim,
            # source} pairs function 41's research brief attached", so the
            # general substring would otherwise claim it.
            #
            # A clean verdict, in the exact shape 48's own prompt
            # prescribes for one: {"pass": true, "violations": [],
            # "notes": ""}.
            content = json.dumps({"pass": True, "violations": [], "notes": ""})
        elif "Insight-to-Story Editor" in system_prompt:
            # Function 39. Like the Research Brief branch above, this did
            # not exist -- the drafting handlers were all exercised against
            # `{}`, a response no schema accepts, which is exactly the
            # double-blindness that hid the input-contract bug this change
            # fixes. Shaped to 39's own output schema.
            content = json.dumps(
                {
                    "post": (
                        "Every finance team has felt it: the same question asked twice, "
                        "answered three ways. One governed source of truth is not a "
                        "dashboard project, it is a consolidation problem. "
                        "Read more below."
                    ),
                    "pillar": json.loads(user_content)["pillar"],
                    "cta_url": _fake_cta_url(user_content),
                }
            )
        elif "Carousel/Document Post Writer" in system_prompt:
            # Function 45. One slide per supplied proof point plus the
            # closing roof-line slide, matching the schema's own
            # "minItems": 2 and the prompt's slide contract.
            proof_points = json.loads(user_content)["proof_points"]
            slides = [
                {
                    "slide_number": index,
                    "headline": f"Proof {index}",
                    "subhead": point[:120],
                }
                for index, point in enumerate(proof_points, start=1)
            ]
            slides.append(
                {
                    "slide_number": len(slides) + 1,
                    "headline": "Your Data. Delivered.",
                    "subhead": "Canvas Intelligence",
                }
            )
            header = "slide_number,headline,subhead,image_ref,brand_template_id"
            rows = [
                f"{slide['slide_number']},{slide['headline']},{slide['subhead']},,"
                for slide in slides
            ]
            content = json.dumps(
                {
                    "slides": slides,
                    "canva_bulk_create_csv": "\n".join([header, *rows]),
                    "cta_url": _fake_cta_url(user_content),
                }
            )
        elif "Email/Newsletter Writer" in system_prompt:
            # Function 46. `body` carries a minLength of 200, so the canned
            # text is padded from the supplied proof points rather than
            # being a stub that would fail the schema it is meant to prove.
            proof_points = json.loads(user_content)["proof_points"]
            body = (
                "This week, one theme kept surfacing in conversations with finance "
                "leaders: consolidation is the work, and reporting is only what it "
                "makes possible.\n\n"
                + "\n\n".join(f"- {point}" for point in proof_points)
                + "\n\nIf any of this is familiar, the link below is the shortest "
                "route to a conversation about what a governed source of truth "
                "would look like in your own group."
            )
            content = json.dumps(
                {
                    "subject": "The number everyone agrees on",
                    "body": body,
                    "cta_url": _fake_cta_url(user_content),
                }
            )
        elif "Content Repurposer" in system_prompt:
            # Function 52. One derivative per requested target format, in
            # the same order, as its schema's own description requires.
            payload = json.loads(user_content)
            content = json.dumps(
                {
                    "derivatives": [
                        {
                            "format": fmt,
                            "post": (
                                "One governed source of truth is a consolidation "
                                f"outcome, not a dashboard one. ({fmt})"
                            ),
                            "cta_url": _fake_cta_url(user_content),
                        }
                        for fmt in payload["target_formats"]
                    ],
                    "pillar": payload["pillar"],
                }
            )
        elif "Research Brief" in system_prompt or "research brief" in system_prompt:
            # Function 41. Added when the weekly loop gained output
            # validation: this branch did not exist, so every weekly
            # handler was previously exercised against `{}` -- a response
            # no function's schema would accept. Shaped to 41's own
            # schema, proof points included, so the drafting handoff has
            # something real to carry.
            content = json.dumps(
                {
                    "brief": {
                        "pillar": "Consolidation at scale",
                        "vertical": "logistics & distribution",
                        "proof_points": [
                            {
                                "claim": "A listed group consolidated 40+ business units "
                                "across 14+ ERP systems into one governed lakehouse",
                                "source": "https://www.moneyweb.co.za/feed/",
                            },
                            {
                                "claim": "Reporting cycles fell from nine days to two",
                                "source": "https://businesstech.co.za/news/feed/",
                            },
                        ],
                        "note": "Built from the week's scored signals.",
                    },
                    "audience_note": "Written for the office of the CFO in multi-entity groups.",
                }
            )
        elif "Brand Steward" in system_prompt:
            payload = json.loads(user_content)
            draft_text = payload.get("draft_text", "")
            channel = payload.get("channel", "linkedin")
            violations = []
            # F-BRIEF-CTA-UTM-EXEMPT (4 Aug 2026, heartbeat round 18,
            # Pieter's ruling: "Go with a for daily briefs"): the
            # internal-brief channel (the daily-signal-loop's own brief,
            # never published externally) is exempt from rule 5 -- see
            # prompt.md checks 4/5 and dispatch.py's qa_review_handler.
            # function 02's rule 5 is "every URL must carry utm params" --
            # vacuously satisfied when the draft has no URL at all (e.g. an
            # internal brief with no CTA link), so only flag when a URL IS
            # present but missing one of the 3 required params.
            has_url = "http://" in draft_text or "https://" in draft_text
            has_all_utm = (
                "utm_source" in draft_text
                and "utm_medium" in draft_text
                and "utm_campaign" in draft_text
            )
            if has_url and not has_all_utm and channel != "internal-brief":
                violations.append("url-utm")
            content = json.dumps(
                {
                    "pass": not violations,
                    "violations": violations,
                    "notes": "seeded fake verdict" if violations else "",
                }
            )
        else:
            content = json.dumps({})

        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": content,
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


class FakeVaultClient:
    """In-memory stand-in for VaultClientExt's public surface."""

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}
        self._signals: dict[str, dict[str, Any]] = {}
        self._opportunity_cards: dict[str, dict[str, Any]] = {}
        self._briefs: dict[str, dict[str, Any]] = {}
        self._agent_runs: dict[str, dict[str, Any]] = {}
        self._assets: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> "FakeVaultClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def get_or_create_campaign(self, run_name: str, *, function_id: str) -> str:
        for cid, row in self._campaigns.items():
            if row["name"] == run_name:
                return cid
        cid = str(uuid.uuid4())
        self._campaigns[cid] = {"id": cid, "name": run_name, "function_id": function_id}
        return cid

    def create_signal(self, *, source, signal_type, payload, campaign_id, function_id) -> dict:
        sid = str(uuid.uuid4())
        row = {
            "id": sid,
            "source": source,
            "signal_type": signal_type,
            "payload": payload,
            # The real Vault stamps this server-side; carried here so
            # ingest-signals' horizon filter has a real field to read.
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        self._signals[sid] = row
        return row

    def get_signal(self, signal_id: str) -> dict:
        return self._signals[signal_id]

    def list_signals(self, *, limit: int = 100) -> list[dict]:
        """Newest-first, mirroring the real GET /signals ordering, so
        ingest-signals' cross-run memory sees the same shape it will in
        production."""
        return list(reversed(list(self._signals.values())))[:limit]

    def create_opportunity_card(
        self, *, signal_id, title, score, campaign_id, function_id, status="new"
    ) -> dict:
        cid = str(uuid.uuid4())
        row = {
            "id": cid,
            "signal_id": signal_id,
            "title": title,
            "score": score,
            "status": status,
        }
        self._opportunity_cards[cid] = row
        return row

    def create_brief(
        self, *, title, body_text, campaign_id, function_id, opportunity_card_id=None
    ) -> dict:
        bid = str(uuid.uuid4())
        row = {"id": bid, "title": title, "body": body_text}
        self._briefs[bid] = row
        return row

    def get_brief(self, brief_id: str) -> dict:
        return self._briefs[brief_id]

    def create_agent_run(
        self,
        *,
        agent_name,
        campaign_id,
        function_id,
        status="succeeded",
        input_payload=None,
        output_payload=None,
    ) -> dict:
        aid = str(uuid.uuid4())
        row = {
            "id": aid,
            "agent_name": agent_name,
            "status": status,
            "input": input_payload or {},
            "output": output_payload or {},
        }
        self._agent_runs[aid] = row
        return row

    def get_agent_run(self, agent_run_id: str) -> dict:
        return self._agent_runs[agent_run_id]

    def update_agent_run(
        self, agent_run_id, *, status=None, output_payload=None, completed_at=None
    ) -> dict:
        row = self._agent_runs[agent_run_id]
        if status is not None:
            row["status"] = status
        if output_payload is not None:
            row["output"] = output_payload
        return row

    def create_asset(
        self,
        *,
        asset_type,
        agent_run_id,
        campaign_id,
        function_id,
        content_bytes,
        brief_id=None,
        predecessor_asset_id=None,
        approval_state="draft",
    ) -> dict:
        import hashlib

        aid = str(uuid.uuid4())
        row = {
            "id": aid,
            "asset_type": asset_type,
            "agent_run_id": agent_run_id,
            "content_base64": base64.b64encode(content_bytes).decode("ascii"),
            "content_hash": hashlib.sha256(content_bytes).hexdigest(),
            "approval_state": approval_state,
        }
        self._assets[aid] = row
        return row

    def get_asset(self, asset_id: str) -> dict:
        return self._assets[asset_id]

    def get_cost(self, cost_id: str) -> dict:
        return {"id": cost_id, "amount": 0.0}


class FakeGatekeeperClient:
    def __enter__(self) -> "FakeGatekeeperClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def gate_check(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "decision_id": str(uuid.uuid4()),
            "agent_run_id": kwargs.get("agent_run_id"),
            "outcome": "escalated",
            "reason": "level_1_requires_approval",
            "level": 1,
            "function_id": kwargs.get("function_id"),
            "action_class": kwargs.get("action_class"),
            "approval_id": str(uuid.uuid4()),
            "approve_url": "https://approval.invalid/approval-action/fake?choice=approve",
            "reject_url": "https://approval.invalid/approval-action/fake?choice=reject",
        }

    def get_approval_status(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "pending", "decided_by": None, "decided_at": None}


class FakeMCPClient:
    def __enter__(self) -> "FakeMCPClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"source": "fixture", "url": arguments.get("url"), "body": "fake fetched content"}


def patch_dispatch_clients(
    monkeypatch: Any, *, shared_vault: "FakeVaultClient | None" = None
) -> FakeVaultClient:
    """One-stop monkeypatch installing all 4 fakes over dispatch.py's
    build_*_client() factories.

    `shared_vault` is the SAME FakeVaultClient instance across every
    handler invocation in a test run — a real Vault deployment persists
    data between handler calls (each handler opens its own httpx.Client
    but talks to the same server), so a fresh empty fake per call would
    break any test exercising more than one handler in the same lineage
    chain (e.g. draft-brief reading back a signal ingest-signals created
    earlier in the same run). Returns the shared instance so a test can
    inspect it afterwards.
    """
    from orchestrator import dispatch

    vault = shared_vault if shared_vault is not None else FakeVaultClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: FakeGatewayClient())
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: vault)
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: FakeGatekeeperClient())
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: FakeMCPClient())
    return vault
