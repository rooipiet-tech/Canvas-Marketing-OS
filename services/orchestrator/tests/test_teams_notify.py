"""F-TEAMS-CARD-REVIEW-LINK (11 Aug 2026): coverage for the "Review in
console" Action.OpenUrl button added to build_needs_edit_card and
build_retry_exhausted_card, and its CMOS_CONSOLE_BASE_URL-gated resolution
in notify_needs_edit / notify_retry_exhausted.

No prior dedicated unit test file exercised teams_notify.py's card
builders directly (test_dispatch_qa_retry_loop.py only monkeypatches the
notify_* functions to capture their kwargs) -- this file fills that gap
for the one behavior this change actually touches, without re-testing the
pre-existing AC-25 no-webhook-no-POST behavior those other tests already
cover indirectly.
"""

from __future__ import annotations

from orchestrator import teams_notify


def test_needs_edit_card_has_no_actions_block_by_default() -> None:
    """Backward compatibility: no review_url means today's exact shape --
    no "actions" key at all, not an empty list."""
    card = teams_notify.build_needs_edit_card(
        task_id="t-1", channel="linkedin", violations=["unsupported-claim"], draft_excerpt="..."
    )
    content = card["attachments"][0]["content"]
    assert "actions" not in content


def test_needs_edit_card_adds_review_button_when_review_url_given() -> None:
    card = teams_notify.build_needs_edit_card(
        task_id="t-1",
        channel="linkedin",
        violations=["unsupported-claim"],
        draft_excerpt="...",
        review_url="https://console.example/review/t-1",
    )
    content = card["attachments"][0]["content"]
    assert content["actions"] == [
        {
            "type": "Action.OpenUrl",
            "title": "Review in console",
            "url": "https://console.example/review/t-1",
        }
    ]


def test_retry_exhausted_card_has_no_actions_block_by_default() -> None:
    card = teams_notify.build_retry_exhausted_card(
        task_id="t-1",
        draft_task_id="t-0",
        channel="linkedin",
        violations=["unsupported-claim"],
        original_excerpt="a",
        revised_excerpt="b",
        diff_text="",
        attempts=3,
        hollowed=False,
    )
    content = card["attachments"][0]["content"]
    assert "actions" not in content


def test_retry_exhausted_card_links_off_qa_task_id_not_draft_task_id() -> None:
    """GET /tasks/{task_id}/review resolves the draft via lineage from the
    QA task_id -- the review link must point at task_id, never
    draft_task_id, even though both are shown in the card body."""
    card = teams_notify.build_retry_exhausted_card(
        task_id="qa-task-1",
        draft_task_id="draft-task-1",
        channel="linkedin",
        violations=["unsupported-claim"],
        original_excerpt="a",
        revised_excerpt="b",
        diff_text="",
        attempts=3,
        hollowed=False,
        review_url="https://console.example/review/qa-task-1",
    )
    content = card["attachments"][0]["content"]
    assert content["actions"][0]["url"] == "https://console.example/review/qa-task-1"


def test_console_base_url_reads_env_var(monkeypatch) -> None:
    monkeypatch.delenv("CMOS_CONSOLE_BASE_URL", raising=False)
    assert teams_notify.console_base_url() is None

    monkeypatch.setenv("CMOS_CONSOLE_BASE_URL", "https://console.example/")
    assert teams_notify.console_base_url() == "https://console.example/"


def test_notify_needs_edit_resolves_review_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CMOS_CONSOLE_BASE_URL", "https://console.example")
    posted = []

    teams_notify.notify_needs_edit(
        task_id="t-1",
        channel="linkedin",
        violations=["unsupported-claim"],
        draft_excerpt="...",
        webhook_url="https://teams.example/hook",
        http_post=lambda url, json, timeout: posted.append(json),
    )

    assert len(posted) == 1
    content = posted[0]["attachments"][0]["content"]
    assert content["actions"] == [
        {
            "type": "Action.OpenUrl",
            "title": "Review in console",
            "url": "https://console.example/review/t-1",
        }
    ]


def test_notify_needs_edit_omits_actions_when_console_base_url_unset(monkeypatch) -> None:
    monkeypatch.delenv("CMOS_CONSOLE_BASE_URL", raising=False)
    posted = []

    teams_notify.notify_needs_edit(
        task_id="t-1",
        channel="linkedin",
        violations=["unsupported-claim"],
        draft_excerpt="...",
        webhook_url="https://teams.example/hook",
        http_post=lambda url, json, timeout: posted.append(json),
    )

    assert len(posted) == 1
    content = posted[0]["attachments"][0]["content"]
    assert "actions" not in content


def test_notify_retry_exhausted_resolves_review_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CMOS_CONSOLE_BASE_URL", "https://console.example")
    posted = []

    teams_notify.notify_retry_exhausted(
        task_id="qa-task-1",
        draft_task_id="draft-task-1",
        channel="linkedin",
        violations=["unsupported-claim"],
        original_excerpt="a",
        revised_excerpt="b",
        diff_text="",
        attempts=3,
        hollowed=False,
        webhook_url="https://teams.example/hook",
        http_post=lambda url, json, timeout: posted.append(json),
    )

    assert len(posted) == 1
    content = posted[0]["attachments"][0]["content"]
    assert content["actions"][0]["url"] == "https://console.example/review/qa-task-1"


def test_notify_review_url_override_takes_precedence_over_env(monkeypatch) -> None:
    monkeypatch.setenv("CMOS_CONSOLE_BASE_URL", "https://console.example")
    posted = []

    teams_notify.notify_needs_edit(
        task_id="t-1",
        channel="linkedin",
        violations=["unsupported-claim"],
        draft_excerpt="...",
        webhook_url="https://teams.example/hook",
        review_url="https://override.example/custom",
        http_post=lambda url, json, timeout: posted.append(json),
    )

    content = posted[0]["attachments"][0]["content"]
    assert content["actions"][0]["url"] == "https://override.example/custom"
