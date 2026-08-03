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
from typing import Any


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
        elif "Brand Steward" in system_prompt:
            payload = json.loads(user_content)
            draft_text = payload.get("draft_text", "")
            violations = []
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
            if has_url and not has_all_utm:
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
        self._briefs: dict[str, dict[str, Any]] = {}
        self._agent_runs: dict[str, dict[str, Any]] = {}
        self._assets: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> "FakeVaultClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def get_or_create_campaign(self, run_name: str, *, campaign_uuid: str, function_id: str) -> str:
        for cid, row in self._campaigns.items():
            if row["name"] == run_name:
                return cid
        cid = str(uuid.uuid4())
        self._campaigns[cid] = {
            "id": cid,
            "name": run_name,
            "campaign_uuid": campaign_uuid,
            "function_id": function_id,
        }
        return cid

    def create_signal(self, *, source, signal_type, payload, campaign_id, function_id) -> dict:
        sid = str(uuid.uuid4())
        row = {"id": sid, "source": source, "signal_type": signal_type, "payload": payload}
        self._signals[sid] = row
        return row

    def get_signal(self, signal_id: str) -> dict:
        return self._signals[signal_id]

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
