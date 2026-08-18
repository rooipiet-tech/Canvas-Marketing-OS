"""Regression test for F-CAROUSEL-CSV-LEAKS-INTO-TEAMS (15 Aug 2026).

_render_carousel() appends a machine-oriented Canva bulk-upload CSV block
after a "--- canva_bulk_create_csv ---" marker. Nothing downstream ever
parses that CSV back out of draft_text -- confirmed via a repo-wide
search -- so it's safe to drop when draft_text feeds a Teams excerpt or a
diff between QA-retry attempts. Before the fix, dispatch.py sliced/diffed
the FULL rendered text (CSV included), which Pieter confirmed live: a
QA-retry-exhausted card for a carousel draft rendered raw CSV rows and
diff-hunk noise in its "excerpt"/"track changes" blocks instead of
readable slide copy.

_teams_display_text() is the fix: strip everything from the CSV marker
onward before a carousel's draft_text is excerpted or diffed for Teams,
while leaving the stored draft_text (Vault, the console review page, the
approval inbox) completely untouched -- only what feeds Teams notify is
touched.
"""

from __future__ import annotations

from orchestrator import dispatch


def _sample_carousel_output() -> dict:
    return {
        "slides": [
            {
                "slide_number": 1,
                "headline": "More than 3 days a month. Gone.",
                "subhead": "Finance teams tell us they spend over 3 days a month "
                "not on decisions -- but on cleaning data.",
            },
            {
                "slide_number": 2,
                "headline": "A different number for the same question.",
                "subhead": "Finance says one thing. Operations says another.",
            },
        ],
        "cta_url": "https://www.canvasintelligence.com/productised-speed"
        "?utm_source=linkedin&utm_medium=carousel&utm_campaign=productised-speed-pillar",
        "canva_bulk_create_csv": (
            "slide_number,headline,subhead,image_slide_ref,template_id\n"
            "1,More than 3 days a month. Gone.,Finance teams...,"
            "img_slide_01,TMPL_CI_CAROUSEL_001\n"
            "2,A different number for the same question.,Finance says...,"
            "img_slide_02,TMPL_CI_CAROUSEL_001\n"
        ),
    }


class TestRenderCarouselStillIncludesCsv:
    """_render_carousel's own output is unchanged -- Vault storage, the
    console review page, and any human copying the bulk-upload CSV out of
    the stored draft still see the full text."""

    def test_full_render_still_contains_the_csv_block(self) -> None:
        rendered = dispatch._render_carousel(_sample_carousel_output())
        assert dispatch.CAROUSEL_BULK_CSV_MARKER in rendered
        assert "TMPL_CI_CAROUSEL_001" in rendered
        assert "[CAROUSEL]" in rendered
        assert "Slide 1: More than 3 days a month. Gone." in rendered


class TestTeamsDisplayTextStripsBulkCsv:
    def test_strips_everything_from_the_csv_marker_onward(self) -> None:
        rendered = dispatch._render_carousel(_sample_carousel_output())
        display = dispatch._teams_display_text(rendered)

        assert dispatch.CAROUSEL_BULK_CSV_MARKER not in display
        assert "TMPL_CI_CAROUSEL_001" not in display
        assert "img_slide_01" not in display
        # The human-readable parts survive: slide copy and the CTA line.
        assert "Slide 1: More than 3 days a month. Gone." in display
        assert "Slide 2: A different number for the same question." in display
        assert "CTA: https://www.canvasintelligence.com/productised-speed" in display

    def test_non_carousel_text_passes_through_unchanged(self) -> None:
        # Simple LinkedIn posts never call _render_carousel and never
        # contain the marker -- _teams_display_text must be a no-op here,
        # not just "no crash".
        plain = "Multi-entity CFOs don't need another dashboard. They need one number."
        assert dispatch._teams_display_text(plain) == plain

    def test_a_diff_between_two_carousel_attempts_excludes_csv_noise(self) -> None:
        import difflib

        original = dispatch._render_carousel(_sample_carousel_output())
        revised_output = _sample_carousel_output()
        revised_output["slides"][0]["headline"] = "More than 3 days a month. Gone. Really."
        revised = dispatch._render_carousel(revised_output)

        original_display = dispatch._teams_display_text(original)
        revised_display = dispatch._teams_display_text(revised)
        diff_text = "\n".join(
            difflib.unified_diff(
                original_display.splitlines(),
                revised_display.splitlines(),
                fromfile="original",
                tofile="revised",
                lineterm="",
            )
        )

        # The real change (the headline edit) is visible...
        assert "Really" in diff_text
        # ...and no CSV/template-id noise leaked into the diff, even
        # though the CSV blob is byte-identical between the two full
        # renders and would otherwise contribute zero *meaningful* diff
        # lines while still bloating/obscuring the output.
        assert "TMPL_CI_CAROUSEL_001" not in diff_text
        assert dispatch.CAROUSEL_BULK_CSV_MARKER not in diff_text
