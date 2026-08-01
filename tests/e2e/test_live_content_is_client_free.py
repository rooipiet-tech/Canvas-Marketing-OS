"""AC-06(b) — the Vault asset row / draft payload that actually reaches
the Publisher/Buffer step in the live run carries zero client_references
(DE-4: the loop's real published content is client-free by construction,
never relying on the uncleared-client-block firing on the live path).
"""

from __future__ import annotations

import base64
from typing import Any

REGISTERED_CLIENT_NAMES_AND_ALIASES = (
    "Imperial",
    "Imperial Logistics",
    "Rotork",
    "Weir",
    "The Weir Group",
    "ArcelorMittal",
    "ArcelorMittal SA",
    "SGB Cape",
    "SGB-Cape",
    "Delta",
    "Delta Corporation",
    "Delta Beverages",
)


def test_draft_content_asset_names_no_registered_client(
    live_daily_loop_run: dict[str, Any], env_vars_configured: dict[str, str]
) -> None:
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    orchestrator_root = repo_root / "services" / "orchestrator"
    if str(orchestrator_root) not in sys.path:
        sys.path.insert(0, str(orchestrator_root))
    from orchestrator import dispatch

    final_state = live_daily_loop_run["final_state"]
    content_stage = next(
        (s for s in final_state.get("stages", []) if s["task_type"] == "draft-content"), None
    )
    assert content_stage is not None, "no draft-content stage found in this run"
    result_ref = content_stage.get("result_ref") or {}
    asset_id = result_ref.get("vault_asset_id")
    assert asset_id, "draft-content stage carries no vault_asset_id"

    with dispatch.build_vault_client() as vault:
        asset = vault.get_asset(asset_id)

    content_base64 = asset.get("content_base64")
    assert content_base64, f"asset {asset_id} has no content_base64"
    text = base64.b64decode(content_base64).decode("utf-8")

    for name in REGISTERED_CLIENT_NAMES_AND_ALIASES:
        assert name.lower() not in text.lower(), (
            f"the live published draft-content asset names {name!r}, a registered "
            "UNCLEARED client -- the loop's real published content must be client-free"
        )
