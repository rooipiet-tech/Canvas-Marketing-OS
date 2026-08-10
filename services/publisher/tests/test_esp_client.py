"""F-NEWSLETTER-ESP (7 Aug 2026) — the provider-agnostic newsletter send path.

The behaviour that matters most here is the one that is hardest to test
after the fact: dry-run must make NO network call at all. Every other
assertion in this file is about shape; that one is about a send to real
inboxes being unrecallable.

HTTP is intercepted with respx so the real httpx path inside the sender is
exercised rather than replaced.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from app.esp_client import (
    MAILERLITE_API_BASE,
    MailchimpSender,
    MailerLiteSender,
    NewsletterSendError,
    build_newsletter_sender,
    is_newsletter_live_mode,
)


def _sender(**overrides) -> MailerLiteSender:
    kwargs = {
        "api_key": "test-key",
        "group_id": "group-123",
        "from_email": "insights@canvasintelligence.com",
        "from_name": "Canvas Intelligence",
    }
    kwargs.update(overrides)
    return MailerLiteSender(**kwargs)


# ---------------------------------------------------------------------------
# Dry-run: the irreversibility guard
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_network_call_at_all():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(host="connect.mailerlite.com")
        receipt = _sender().send(subject="Week 32", html="<p>hello</p>", dry_run=True)

    assert route.call_count == 0
    assert receipt["dry_run"] is True
    assert receipt["provider"] == "mailerlite"
    assert receipt["body_bytes"] == len(b"<p>hello</p>")


def test_dry_run_does_not_require_credentials():
    """A dry-run must still work on a deploy with no ESP secret, or the
    default (dry-run) posture would fail every publish attempt."""
    bare = MailerLiteSender(api_key=None, group_id=None, from_email=None)
    receipt = bare.send(subject="Week 32", html="<p>hello</p>", dry_run=True)
    assert receipt["dry_run"] is True


# ---------------------------------------------------------------------------
# Live send
# ---------------------------------------------------------------------------


def test_live_send_creates_then_schedules_instantly():
    with respx.mock as mock:
        create = mock.post(f"{MAILERLITE_API_BASE}/campaigns").mock(
            return_value=httpx.Response(201, json={"data": {"id": "camp-9"}})
        )
        schedule = mock.post(f"{MAILERLITE_API_BASE}/campaigns/camp-9/schedule").mock(
            return_value=httpx.Response(200, json={"data": {"id": "camp-9", "status": "ready"}})
        )
        receipt = _sender().send(
            subject="Month-end, two days faster", html="<p>body</p>", dry_run=False
        )

    assert create.call_count == 1
    assert schedule.call_count == 1

    body = json.loads(create.calls[0].request.content)
    assert body["type"] == "regular"
    assert body["groups"] == ["group-123"]
    assert body["emails"][0]["subject"] == "Month-end, two days faster"
    assert body["emails"][0]["from"] == "insights@canvasintelligence.com"
    assert body["emails"][0]["content"] == "<p>body</p>"
    assert create.calls[0].request.headers["Authorization"] == "Bearer test-key"

    assert json.loads(schedule.calls[0].request.content) == {"delivery": "instant"}
    assert receipt == {
        "provider": "mailerlite",
        "dry_run": False,
        "campaign_id": "camp-9",
        "subject": "Month-end, two days faster",
        "group_id": "group-123",
    }


def test_campaign_name_defaults_to_subject_and_is_truncated():
    long_subject = "x" * 400
    with respx.mock as mock:
        create = mock.post(f"{MAILERLITE_API_BASE}/campaigns").mock(
            return_value=httpx.Response(201, json={"data": {"id": "camp-1"}})
        )
        mock.post(f"{MAILERLITE_API_BASE}/campaigns/camp-1/schedule").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        _sender().send(subject=long_subject, html="<p>b</p>", dry_run=False)

    body = json.loads(create.calls[0].request.content)
    assert len(body["name"]) == 255
    assert len(body["emails"][0]["subject"]) == 255


def test_create_failure_never_schedules():
    with respx.mock as mock:
        mock.post(f"{MAILERLITE_API_BASE}/campaigns").mock(
            return_value=httpx.Response(422, text="content requires a higher plan")
        )
        schedule = mock.post(f"{MAILERLITE_API_BASE}/campaigns/camp-1/schedule")

        with pytest.raises(NewsletterSendError, match="higher plan"):
            _sender().send(subject="Week 32", html="<p>b</p>", dry_run=False)

    assert schedule.call_count == 0


def test_schedule_failure_reports_that_a_draft_was_left_behind():
    """Operationally important: a created-but-unscheduled campaign is
    recoverable by hand, and the error has to say so."""
    with respx.mock as mock:
        mock.post(f"{MAILERLITE_API_BASE}/campaigns").mock(
            return_value=httpx.Response(201, json={"data": {"id": "camp-7"}})
        )
        mock.post(f"{MAILERLITE_API_BASE}/campaigns/camp-7/schedule").mock(
            return_value=httpx.Response(500, text="upstream error")
        )
        with pytest.raises(NewsletterSendError, match="reviewable draft"):
            _sender().send(subject="Week 32", html="<p>b</p>", dry_run=False)


def test_create_returning_no_id_is_an_error_not_a_silent_success():
    with respx.mock as mock:
        mock.post(f"{MAILERLITE_API_BASE}/campaigns").mock(
            return_value=httpx.Response(201, json={"data": {}})
        )
        with pytest.raises(NewsletterSendError, match="no campaign id"):
            _sender().send(subject="Week 32", html="<p>b</p>", dry_run=False)


# ---------------------------------------------------------------------------
# Configuration and validation
# ---------------------------------------------------------------------------


def test_live_send_without_credentials_raises_before_any_call():
    bare = MailerLiteSender(api_key=None, group_id=None, from_email=None)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(host="connect.mailerlite.com")
        with pytest.raises(NewsletterSendError, match="MAILERLITE_API_KEY"):
            bare.send(subject="Week 32", html="<p>b</p>", dry_run=False)
    assert route.call_count == 0


@pytest.mark.parametrize(
    ("subject", "html", "match"),
    [("", "<p>b</p>", "subject is empty"), ("Week 32", "   ", "body is empty")],
)
def test_empty_subject_or_body_is_refused(subject, html, match):
    with pytest.raises(NewsletterSendError, match=match):
        _sender().send(subject=subject, html=html, dry_run=False)


def test_is_newsletter_live_mode_follows_the_configured_provider_secret(monkeypatch):
    monkeypatch.delenv("MAILERLITE_API_KEY", raising=False)
    monkeypatch.delenv("MAILCHIMP_API_KEY", raising=False)
    monkeypatch.delenv("NEWSLETTER_ESP_PROVIDER", raising=False)
    assert is_newsletter_live_mode() is False

    monkeypatch.setenv("MAILCHIMP_API_KEY", "k-us6")
    assert is_newsletter_live_mode() is True


def test_the_other_providers_secret_does_not_count_as_live(monkeypatch):
    """Default provider is Mailchimp. A leftover MailerLite key from the
    earlier ruling must not make the service think it can send."""
    monkeypatch.delenv("MAILCHIMP_API_KEY", raising=False)
    monkeypatch.delenv("NEWSLETTER_ESP_PROVIDER", raising=False)
    monkeypatch.setenv("MAILERLITE_API_KEY", "k")
    assert is_newsletter_live_mode() is False

    monkeypatch.setenv("NEWSLETTER_ESP_PROVIDER", "mailerlite")
    assert is_newsletter_live_mode() is True


def test_unknown_provider_is_never_live(monkeypatch):
    monkeypatch.setenv("NEWSLETTER_ESP_PROVIDER", "mailchip")
    monkeypatch.setenv("MAILCHIMP_API_KEY", "k-us6")
    assert is_newsletter_live_mode() is False


def test_build_newsletter_sender_defaults_to_mailchimp(monkeypatch):
    monkeypatch.delenv("NEWSLETTER_ESP_PROVIDER", raising=False)
    assert isinstance(build_newsletter_sender(), MailchimpSender)


def test_build_newsletter_sender_honours_the_provider_override(monkeypatch):
    monkeypatch.setenv("NEWSLETTER_ESP_PROVIDER", "mailerlite")
    assert isinstance(build_newsletter_sender(), MailerLiteSender)


def test_unknown_provider_raises_rather_than_falling_back(monkeypatch):
    """A typo in NEWSLETTER_ESP_PROVIDER must not quietly send through a
    provider nobody chose."""
    monkeypatch.setenv("NEWSLETTER_ESP_PROVIDER", "mailchip")
    with pytest.raises(NewsletterSendError, match="unknown newsletter ESP provider"):
        build_newsletter_sender()


# ---------------------------------------------------------------------------
# Mailchimp adapter
# ---------------------------------------------------------------------------

MC_BASE = "https://us6.api.mailchimp.com/3.0"


def _mc(**overrides) -> MailchimpSender:
    kwargs = {
        "api_key": "0123456789abcdef0123456789abcde-us6",
        "audience_id": "aud123",
        "from_email": "insights@canvasintelligence.com",
        "from_name": "Canvas Intelligence",
    }
    kwargs.update(overrides)
    return MailchimpSender(**kwargs)


def _mc_happy_path(mock, *, is_ready: bool = True, items: list | None = None):
    create = mock.post(f"{MC_BASE}/campaigns").mock(
        return_value=httpx.Response(200, json={"id": "camp-mc-1"})
    )
    content = mock.put(f"{MC_BASE}/campaigns/camp-mc-1/content").mock(
        return_value=httpx.Response(200, json={"html": "<p>b</p>"})
    )
    checklist = mock.get(f"{MC_BASE}/campaigns/camp-mc-1/send-checklist").mock(
        return_value=httpx.Response(200, json={"is_ready": is_ready, "items": items or []})
    )
    send = mock.post(f"{MC_BASE}/campaigns/camp-mc-1/actions/send").mock(
        return_value=httpx.Response(204)
    )
    return create, content, checklist, send


def test_mailchimp_derives_the_datacentre_from_the_key():
    assert MailchimpSender.datacentre_from_key("abc-us19") == "us19"
    assert MailchimpSender.datacentre_from_key("nodashhere") is None
    assert _mc().base_url() == MC_BASE


def test_mailchimp_key_without_datacentre_suffix_is_a_clear_error():
    with pytest.raises(NewsletterSendError, match="no datacentre suffix"):
        _mc(api_key="keywithoutsuffix").base_url()


def test_mailchimp_send_creates_sets_content_checks_then_sends():
    with respx.mock as mock:
        create, content, checklist, send = _mc_happy_path(mock)
        receipt = _mc().send(subject="Week 32", html="<p>b</p>", dry_run=False)

    assert (create.call_count, content.call_count, checklist.call_count, send.call_count) == (
        1,
        1,
        1,
        1,
    )
    body = json.loads(create.calls[0].request.content)
    assert body["type"] == "regular"
    assert body["recipients"]["list_id"] == "aud123"
    assert body["settings"]["subject_line"] == "Week 32"
    assert body["settings"]["reply_to"] == "insights@canvasintelligence.com"
    assert body["settings"]["auto_footer"] is False
    assert json.loads(content.calls[0].request.content) == {"html": "<p>b</p>"}
    assert receipt["campaign_id"] == "camp-mc-1"
    assert receipt["provider"] == "mailchimp"


def test_mailchimp_refuses_to_send_when_the_checklist_is_not_ready():
    """actions/send is one-shot and irreversible. A campaign Mailchimp
    itself says is broken must never reach it."""
    with respx.mock as mock:
        _, _, _, send = _mc_happy_path(
            mock,
            is_ready=False,
            items=[
                {
                    "type": "error",
                    "id": 1,
                    "heading": "Audience address",
                    "details": "You must set a physical mailing address.",
                }
            ],
        )
        with pytest.raises(NewsletterSendError, match="physical mailing address"):
            _mc().send(subject="Week 32", html="<p>b</p>", dry_run=False)

    assert send.call_count == 0


def test_mailchimp_unreadable_checklist_also_blocks_the_send():
    with respx.mock as mock:
        mock.post(f"{MC_BASE}/campaigns").mock(
            return_value=httpx.Response(200, json={"id": "camp-mc-1"})
        )
        mock.put(f"{MC_BASE}/campaigns/camp-mc-1/content").mock(
            return_value=httpx.Response(200, json={})
        )
        mock.get(f"{MC_BASE}/campaigns/camp-mc-1/send-checklist").mock(
            return_value=httpx.Response(500, text="boom")
        )
        send = mock.post(f"{MC_BASE}/campaigns/camp-mc-1/actions/send")

        with pytest.raises(NewsletterSendError, match="refusing to send unchecked"):
            _mc().send(subject="Week 32", html="<p>b</p>", dry_run=False)

    assert send.call_count == 0


def test_mailchimp_accepts_204_from_send():
    with respx.mock as mock:
        _mc_happy_path(mock)
        receipt = _mc().send(subject="Week 32", html="<p>b</p>", dry_run=False)
    assert receipt["dry_run"] is False


def test_mailchimp_content_failure_leaves_a_reviewable_draft():
    with respx.mock as mock:
        mock.post(f"{MC_BASE}/campaigns").mock(
            return_value=httpx.Response(200, json={"id": "camp-mc-1"})
        )
        mock.put(f"{MC_BASE}/campaigns/camp-mc-1/content").mock(
            return_value=httpx.Response(400, json={"detail": "bad html", "instance": "uuid-1"})
        )
        with pytest.raises(NewsletterSendError, match="reviewable draft"):
            _mc().send(subject="Week 32", html="<p>b</p>", dry_run=False)


def test_mailchimp_error_detail_surfaces_field_errors_and_instance():
    with respx.mock as mock:
        mock.post(f"{MC_BASE}/campaigns").mock(
            return_value=httpx.Response(
                400,
                json={
                    "title": "Invalid Resource",
                    "detail": "Your merge fields were invalid.",
                    "instance": "uuid-9",
                    "errors": [{"field": "settings.reply_to", "message": "Please enter a value"}],
                },
            )
        )
        with pytest.raises(NewsletterSendError) as excinfo:
            _mc().send(subject="Week 32", html="<p>b</p>", dry_run=False)

    message = str(excinfo.value)
    assert "Your merge fields were invalid." in message
    assert "settings.reply_to" in message
    assert "uuid-9" in message


def test_mailchimp_dry_run_makes_no_network_call():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(host="us6.api.mailchimp.com")
        receipt = _mc().send(subject="Week 32", html="<p>b</p>", dry_run=True)
    assert route.call_count == 0
    assert receipt == {
        "provider": "mailchimp",
        "dry_run": True,
        "subject": "Week 32",
        "audience_id": "aud123",
        "body_bytes": len(b"<p>b</p>"),
    }


def test_mailchimp_live_send_without_credentials_raises_before_any_call():
    bare = MailchimpSender(api_key=None, audience_id=None, from_email=None)
    with pytest.raises(NewsletterSendError, match="MAILCHIMP_API_KEY"):
        bare.send(subject="Week 32", html="<p>b</p>", dry_run=False)
