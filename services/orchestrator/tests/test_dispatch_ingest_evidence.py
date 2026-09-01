"""Tests for ingest-signals' evidence shaping (F-INGEST-EVIDENCE-WINDOW).

What the model got to reason over was the first 2000 characters of each
fetched body, RAW -- and 3 of fetch_sources.yaml's 4 URLs are RSS feeds,
where those first characters are largely <channel> preamble rather than a
single article. The scan was asked for 3 attributed signals across 2
domains from an evidence set that was mostly markup.

Feed bodies are now reduced to their items and pages have their markup
stripped BEFORE the per-source budget is applied, so the budget buys
article text. These tests pin that shaping, and pin the budget as
config-driven rather than a literal in the handler.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

# A realistic RSS shape: ~everything before the first <item> is channel
# preamble, which is exactly what the old 2000-char raw slice spent its
# budget on.
RSS_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Moneyweb</title>
    <link>https://www.moneyweb.co.za</link>
    <description>South African business and financial news</description>
    <language>en</language>
    <ttl>15</ttl>
    <image><url>https://www.moneyweb.co.za/logo.png</url><title>Moneyweb</title></image>
    <atom:link href="https://www.moneyweb.co.za/feed/" rel="self" type="application/rss+xml"/>
    <item>
      <title>JSE-listed group consolidates 14 ERPs onto one platform</title>
      <link>https://www.moneyweb.co.za/news/one</link>
      <pubDate>Mon, 18 Aug 2026 06:00:00 +0200</pubDate>
      <description><![CDATA[<p>The group said reporting cycles fell from
      nine days to two &amp; the finance team stopped reconciling by hand.</p>]]></description>
    </item>
    <item>
      <title>CFO survey: three days a month lost to reconciliation</title>
      <link>https://www.moneyweb.co.za/news/two</link>
      <pubDate>Sun, 17 Aug 2026 06:00:00 +0200</pubDate>
      <description>Finance leaders report conflicting numbers across systems.</description>
    </item>
  </channel>
</rss>"""

ATOM_BODY = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>What's new in Fabric</title>
  <entry>
    <title>Direct Lake now supports larger models</title>
    <updated>2026-08-18T09:00:00Z</updated>
    <summary>Capacity limits raised for Direct Lake semantic models.</summary>
  </entry>
</feed>"""

HTML_BODY = """<!doctype html><html><head><title>What's new</title>
<style>.nav{display:none}</style><script>var telemetry=1;</script></head>
<body><nav>Skip to main content</nav>
<h1>What&#39;s new in Microsoft Fabric</h1>
<p>Mirroring is now generally available for additional sources.</p>
</body></html>"""


def test_feed_body_is_reduced_to_items_not_channel_preamble():
    shaped = dispatch._shape_source_evidence(RSS_BODY, 8000)

    assert "JSE-listed group consolidates 14 ERPs onto one platform" in shaped
    assert "CFO survey: three days a month lost to reconciliation" in shaped
    # Channel-level furniture is gone: it is not evidence, it is chrome.
    assert "application/rss+xml" not in shaped
    assert "<ttl>" not in shaped and "logo.png" not in shaped
    # One line per item, so the model sees a list, not a wall.
    assert len(shaped.splitlines()) == 2


def test_feed_items_keep_date_title_and_summary_with_entities_decoded():
    shaped = dispatch._shape_source_evidence(RSS_BODY, 8000)
    first = shaped.splitlines()[0]

    assert "Mon, 18 Aug 2026" in first
    # CDATA unwrapped, inner tags dropped, &amp; decoded.
    assert "reporting cycles fell from nine days to two & the finance team" in first
    assert "<p>" not in first and "CDATA" not in first


def test_atom_entries_are_shaped_like_rss_items():
    shaped = dispatch._shape_source_evidence(ATOM_BODY, 8000)

    assert "Direct Lake now supports larger models" in shaped
    assert "Capacity limits raised" in shaped
    assert "2026-08-18T09:00:00Z" in shaped


def test_non_feed_page_keeps_its_text_and_drops_script_style_and_tags():
    shaped = dispatch._shape_source_evidence(HTML_BODY, 8000)

    assert "What's new in Microsoft Fabric" in shaped  # &#39; decoded
    assert "Mirroring is now generally available" in shaped
    assert "var telemetry" not in shaped
    assert "display:none" not in shaped
    assert "<" not in shaped


def test_plain_text_body_passes_through():
    """A body with no markup -- a plain-text response, or a test double's
    canned string -- must survive shaping untouched."""
    assert dispatch._shape_source_evidence("fake fetched content", 8000) == "fake fetched content"


def test_budget_is_applied_after_shaping_not_before():
    """The point of the change: a tight budget now spends itself on item
    text rather than on preamble it would previously never have got past."""
    shaped = dispatch._shape_source_evidence(RSS_BODY, 120)

    assert len(shaped) == 120
    assert shaped.startswith("Mon, 18 Aug 2026")


def test_malformed_markup_degrades_to_text_instead_of_raising():
    """Third-party feeds are not guaranteed well-formed, and a scan must
    not die on one bad body -- the regex-based shaping has no parse step
    that can fail, which is half of why it is regex-based."""
    shaped = dispatch._shape_source_evidence("<item><title>unclosed title</item", 8000)

    assert "unclosed" in shaped


def test_feed_item_count_is_bounded():
    body = "<rss><channel>" + "".join(
        f"<item><title>Item {n}</title><description>Body {n}</description></item>"
        for n in range(40)
    ) + "</channel></rss>"

    assert len(dispatch._shape_source_evidence(body, 100_000).splitlines()) == (
        dispatch.INGEST_MAX_FEED_ITEMS
    )


class _FeedMCPClient:
    def __enter__(self) -> "_FeedMCPClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def call_tool(self, _tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"source": "live", "url": arguments.get("url"), "body": RSS_BODY}


class _CapturingGatewayClient:
    def __init__(self) -> None:
        from tests.fakes import FakeGatewayClient

        self._inner = FakeGatewayClient()
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_CapturingGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return self._inner.complete(**kw)


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def test_handler_sends_shaped_evidence_and_its_own_output_ceiling(clients, monkeypatch):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _FeedMCPClient())
    gateway = _CapturingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert db.get_task(task_id)["state"] == "completed"
    sent = gateway.calls[0]["user_content"]
    assert "JSE-listed group consolidates 14 ERPs" in sent
    assert "application/rss+xml" not in sent
    # A truncated completion fails as invalid JSON, so the output ceiling
    # is raised alongside the input budget rather than left at the
    # client's 1536 default.
    assert gateway.calls[0]["max_tokens"] == dispatch.INGEST_MAX_TOKENS


def test_source_budget_comes_from_fetch_sources_yaml():
    sources = dispatch._load_fetch_sources()

    assert sources["source_chars"] == 8000
    assert dispatch._ingest_source_chars(sources) == 8000
    # Code default applies only when the key is absent.
    assert dispatch._ingest_source_chars({}) == dispatch.DEFAULT_INGEST_SOURCE_CHARS
