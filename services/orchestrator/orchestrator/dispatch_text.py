"""Evidence text shaping for the scan handlers (C1 extraction).

MOVED VERBATIM from orchestrator/dispatch.py as part of the C1 split
(remediation backlog wave 3). This is a PURE MOVE: not a character of the
logic or of the incident history below has been changed, only its
location. `git log --follow orchestrator/dispatch_text.py` reaches the
original history.

WHY THESE FUNCTIONS AND NOT OTHERS. C1's constraint is that the
orchestrator suite must pass unchanged, and ~98 test call sites
monkeypatch symbols on the `dispatch` module itself (build_gateway_client,
_resolve_scan_profile and eight more). Python resolves an imported name in
the importing module's namespace, so a handler moved to another module
would no longer see those patches -- the tests would not error, they would
silently exercise the real clients. The functions below are the ones with
no such dependency: pure string in, string out, no client, no database, no
patched symbol. They can move without changing what any test observes.

`_shape_source_evidence` and `INGEST_MAX_FEED_ITEMS` are imported back
into dispatch.py, so `dispatch._shape_source_evidence` and
`dispatch.INGEST_MAX_FEED_ITEMS` keep resolving for the tests that read
them. Reading a re-exported name works fine; only PATCHING one would not,
and nothing patches these.
"""

from __future__ import annotations

import html as html_module
import re

# F-INGEST-EVIDENCE-WINDOW (this change). What the model actually got to
# reason over was the first 2000 characters of each fetched body, RAW --
# and 3 of the market-intelligence profile's 4 URLs are RSS feeds, where those first
# characters are largely <channel> preamble (title, link, ttl, image,
# self-referencing atom:link) rather than a single article. The scan was
# being asked for at least 3 attributed signals across 2 domains from an
# evidence set that was mostly markup.
#
# Two changes, both here rather than in mcp-web: fetch_url stays a generic
# fetch tool with one job, and evidence SHAPING is this handler's concern.
#   1. Feed bodies are reduced to their items (title / summary / date)
#      before the budget is applied, so the budget buys article text.
#   2. Non-feed bodies (learn.microsoft.com's what's-new page) have script,
#      style and tags stripped for the same reason.
#
# Deliberately regex-based, not an XML parser: the input is untrusted
# third-party markup, and ElementTree is vulnerable to entity-expansion
# ("billion laughs") without defusedxml. Matching tags with a bounded
# regex over an already-capped string cannot expand anything, needs no new
# dependency, and this is evidence shaping -- not fidelity parsing, where
# a real parser would be worth the risk.

INGEST_MAX_FEED_ITEMS = 12

_FEED_ITEM_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_FEED_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_FEED_SUMMARY_RE = re.compile(
    r"<(description|summary)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE
)
_FEED_DATE_RE = re.compile(
    r"<(pubDate|published|updated)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE
)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def _clean_markup_text(raw: str) -> str:
    """CDATA unwrapped, tags dropped, entities decoded, whitespace collapsed."""
    text = _CDATA_RE.sub(r"\1", raw)
    text = _TAG_RE.sub(" ", text)
    return " ".join(html_module.unescape(text).split())


def _feed_item_lines(body: str, max_items: int = INGEST_MAX_FEED_ITEMS) -> list[str]:
    """One line per feed item, newest-first as the feed itself ordered them.
    Empty list when the body carries no <item>/<entry> elements, which is
    how a caller tells a feed from a page without sniffing content types."""
    lines: list[str] = []
    for match in _FEED_ITEM_RE.finditer(body):
        block = match.group(2)
        title = _clean_markup_text(m.group(1)) if (m := _FEED_TITLE_RE.search(block)) else ""
        summary = _clean_markup_text(m.group(2)) if (m := _FEED_SUMMARY_RE.search(block)) else ""
        date = _clean_markup_text(m.group(2)) if (m := _FEED_DATE_RE.search(block)) else ""
        parts = [part for part in (date, title, summary) if part]
        if not parts:
            continue
        lines.append(" | ".join(parts))
        if len(lines) >= max_items:
            break
    return lines


def _shape_source_evidence(body: str, source_chars: int) -> str:
    """Feed items where the body is a feed, de-marked-up text otherwise,
    truncated to `source_chars`. A body with no markup at all (a plain-text
    response, or a test double's canned string) passes through unchanged
    apart from whitespace collapsing."""
    lines = _feed_item_lines(body)
    shaped = "\n".join(lines) if lines else _clean_markup_text(_SCRIPT_STYLE_RE.sub(" ", body))
    return shaped[:source_chars]
