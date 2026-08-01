"""AC-08(d) — the LinkedIn channel-id constant's definition site carries
an inline comment mapping all 3 known Buffer channel ids (LinkedIn/
Facebook/X) plus the org id, so the GOAL-prose transposition error that
originally swapped the LinkedIn and X ids can never recur silently.
"""

from __future__ import annotations

from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.py"


def test_config_comment_maps_all_three_channel_ids_and_org() -> None:
    source = CONFIG_PATH.read_text(encoding="utf-8")
    assert "68e73facca3a4e6b746d17b4" in source  # LinkedIn (the corrected id)
    assert "68e74731ca3a4e6b746d2469" in source  # Facebook
    assert "68e745c6ca3a4e6b746d22b2" in source  # X
    assert "68e5f2187fe9a5263a3509ab" in source  # org
    assert "LinkedIn=" in source and "Facebook=" in source and "X=" in source and "org=" in source
