"""AC-08(d) — the LinkedIn channel-id policy file maps all 3 known Buffer
channel ids (LinkedIn/Facebook/X) plus the org id together, so the
GOAL-prose transposition error that originally swapped the LinkedIn and X
ids can never recur silently.

TD-20: this used to check app/config.py's own source (the values lived in
an inline literal there). The mapping now lives in
policy/buffer-channels.yaml — app/config.py just reads it.
"""

from __future__ import annotations

from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[1] / "policy" / "buffer-channels.yaml"


def test_policy_maps_all_three_channel_ids_and_org() -> None:
    source = POLICY_PATH.read_text(encoding="utf-8")
    assert "68e73facca3a4e6b746d17b4" in source  # LinkedIn (the corrected id)
    assert "68e74731ca3a4e6b746d2469" in source  # Facebook
    assert "68e745c6ca3a4e6b746d22b2" in source  # X
    assert "68e5f2187fe9a5263a3509ab" in source  # org
    assert (
        "linkedin_channel_id" in source
        and "facebook_channel_id" in source
        and "x_channel_id" in source
        and "org_id" in source
    )
