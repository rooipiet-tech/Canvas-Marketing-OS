"""app/esp_client.py — the newsletter send path, provider-agnostic.

Written 7 Aug 2026 (F-NEWSLETTER-ESP) after Pieter's ruling to send the
weekly newsletter through a real email service provider rather than
Microsoft Graph Mail.Send. Graph can put a message in a mailbox, but it
has no list management, no unsubscribe handling and no deliverability
tooling -- and the unsubscribe obligation on direct marketing is a POPIA
exposure, not a nicety. An ESP supplies all three.

PROVIDER: MAILCHIMP, per Pieter's ruling of 7 Aug ("pull from Dynamics CRM
into Mailchimp and manage everything from Mailchimp"). MailerLite was this
module's original recommendation and its adapter is retained as a working
fallback, but Mailchimp is the default and the one the CRM sync targets.

The interface earned itself within a day: the provider changed between the
first commit and the second, and the change cost one subclass rather than a
rewrite. Keep it that way -- no Mailchimp-specific type should ever escape
into app/routers/publish.py.

WHAT CHANGING TO MAILCHIMP COSTS, recorded so it is not rediscovered later:
US-only data residency (the longest POPIA s72 cross-border chain of the
candidates evaluated), billing by total contacts INCLUDING unsubscribed
ones, and SA VAT with no self-assessment. All three are accepted trade-offs
of the ruling, not oversights. See claude_integrations-finalisation-2026-08.md.

DRY-RUN IS ENFORCED HERE, NOT ONLY AT THE CALLER. send() takes an explicit
`dry_run` argument and refuses to touch the network when it is true. A
newsletter send is the single most irreversible action in this codebase --
a social post can be deleted, an email that has landed in 400 CFOs'
inboxes cannot be recalled -- so the check is duplicated deliberately
rather than trusted to the router alone. See app/config.py's
publisher_dry_run() for the flag itself and app/vault_lookup.py for the
proof-circuit forced-dry-run rule that also has to hold here.

NOT YET WIRED INTO app/routers/publish.py. Three things have to be true
first, and none of them is code (see
claude_integrations-finalisation-2026-08.md):
  1. A Mailchimp account and audience exist, with the audience's physical
     mailing address set -- Mailchimp's send checklist blocks a send
     without one, and there is no way around it.
  2. The audience is populated by a LAWFUL marketing list, not simply
     every contact in Dynamics. POPIA s69 permits direct electronic
     marketing only to data subjects who consented, or to existing
     customers whose details were obtained in a sale and who were given a
     chance to object. `donotbulkemail = false` in CRM means "nobody has
     ticked the suppression box", which is NOT the same as consent. This
     is a legal decision that must precede the first send.
  3. A governance decision: publish-newsletter currently borrows
     `publish.blog_article` as its gate-check function_id "since no
     email-specific gate-check identifier exists" (orchestrator/
     dispatch.py). A real email send should carry its own
     `publish.newsletter` entry in services/gatekeeper/policy/autonomy.yaml
     so the approval card, the audit trail and the kill switch all name
     the thing actually happening. That is a real, governed entry -- NOT
     the smoke/test-only kind teams/test_policy.py forbids -- but it needs
     explicit sign-off before it ships.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

import httpx

# Secret names follow the established convention: env var first (that is
# how Container Apps injects a Key-Vault-backed secretRef), Key Vault name
# recorded here so docs/credentials-runbook.md and the Bicep stay in step.
MAILERLITE_API_KEY_ENV = "MAILERLITE_API_KEY"
MAILERLITE_API_KEY_SECRET = "mailerlite-api-key"

MAILCHIMP_API_KEY_ENV = "MAILCHIMP_API_KEY"
MAILCHIMP_API_KEY_SECRET = "mailchimp-api-key"

NEWSLETTER_PROVIDER_ENV = "NEWSLETTER_ESP_PROVIDER"
NEWSLETTER_LIST_ID_ENV = "NEWSLETTER_LIST_ID"
NEWSLETTER_FROM_EMAIL_ENV = "NEWSLETTER_FROM_EMAIL"
NEWSLETTER_FROM_NAME_ENV = "NEWSLETTER_FROM_NAME"

DEFAULT_PROVIDER = "mailchimp"
MAILERLITE_API_BASE = "https://connect.mailerlite.com/api"

_REQUEST_TIMEOUT_SECONDS = 30.0


class NewsletterSendError(RuntimeError):
    """The ESP refused the campaign, or the sender is misconfigured."""


class NewsletterSender(ABC):
    """One send of one already-QA'd, already-approved newsletter.

    Implementations must be safe to construct without credentials -- the
    credential check belongs in send(), so a misconfigured deploy fails
    at the moment it tries to act rather than at import time.
    """

    provider_name: str

    @abstractmethod
    def send(
        self,
        *,
        subject: str,
        html: str,
        dry_run: bool,
        campaign_name: str | None = None,
    ) -> dict[str, Any]:
        """Send `html` to the configured list. Returns a provider-shaped
        receipt including at minimum {'provider', 'dry_run'}."""


class MailerLiteSender(NewsletterSender):
    """MailerLite: create a regular campaign, then schedule it instantly.

    Two calls, no SDK. `POST /campaigns` creates it in draft; nothing
    leaves MailerLite until `POST /campaigns/{id}/schedule`. That split is
    useful rather than annoying -- a create that succeeds and a schedule
    that fails leaves a reviewable draft in the account, not a half-sent
    campaign.

    KNOWN PLAN RISK, unresolved at time of writing: MailerLite's campaigns
    API documents the `content` field as available on the Advanced plan
    only, while the pricing page lists the custom HTML editor on every
    tier and names the current paid tiers Comfort ($12) and Power ($25) --
    "Advanced" appears to be a superseded tier name the API docs have not
    caught up with. If Comfort rejects `content`, the fix is Power, not a
    code change. send() surfaces the provider's own error text verbatim so
    that diagnosis takes one run, not an investigation. CONFIRM THE TIER
    WITH MAILERLITE BEFORE COMMITTING TO A PLAN.
    """

    provider_name = "mailerlite"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        group_id: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get(MAILERLITE_API_KEY_ENV)
        self._group_id = group_id or os.environ.get(NEWSLETTER_LIST_ID_ENV)
        self._from_email = from_email or os.environ.get(NEWSLETTER_FROM_EMAIL_ENV)
        self._from_name = from_name or os.environ.get(
            NEWSLETTER_FROM_NAME_ENV, "Canvas Intelligence"
        )
        resolved_base = api_base or os.environ.get("MAILERLITE_API_BASE", MAILERLITE_API_BASE)
        self._api_base = resolved_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _require_config(self) -> None:
        missing = [
            name
            for name, value in (
                (MAILERLITE_API_KEY_ENV, self._api_key),
                (NEWSLETTER_LIST_ID_ENV, self._group_id),
                (NEWSLETTER_FROM_EMAIL_ENV, self._from_email),
            )
            if not value
        ]
        if missing:
            raise NewsletterSendError(
                f"MailerLite sender is not configured: missing {', '.join(missing)}"
            )

    def send(
        self,
        *,
        subject: str,
        html: str,
        dry_run: bool,
        campaign_name: str | None = None,
    ) -> dict[str, Any]:
        if not subject.strip():
            raise NewsletterSendError("newsletter subject is empty")
        if not html.strip():
            raise NewsletterSendError("newsletter body is empty")

        if dry_run:
            # No network call at all. Deliberately reported with enough
            # detail to be useful in a publish_attempts row, but nothing
            # here touches MailerLite.
            return {
                "provider": self.provider_name,
                "dry_run": True,
                "subject": subject,
                "group_id": self._group_id,
                "body_bytes": len(html.encode("utf-8")),
            }

        self._require_config()
        name = campaign_name or subject

        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            create = client.post(
                f"{self._api_base}/campaigns",
                headers=self._headers(),
                json={
                    "name": name[:255],
                    "type": "regular",
                    "emails": [
                        {
                            "subject": subject[:255],
                            "from_name": self._from_name,
                            "from": self._from_email,
                            "content": html,
                        }
                    ],
                    "groups": [self._group_id],
                },
            )
            if create.status_code not in (200, 201):
                raise NewsletterSendError(
                    "MailerLite campaign create failed: "
                    f"HTTP {create.status_code} {create.text[:500]}"
                )

            campaign_id = ((create.json() or {}).get("data") or {}).get("id")
            if not campaign_id:
                raise NewsletterSendError(
                    "MailerLite campaign create returned no campaign id; "
                    "nothing was scheduled"
                )

            schedule = client.post(
                f"{self._api_base}/campaigns/{campaign_id}/schedule",
                headers=self._headers(),
                json={"delivery": "instant"},
            )
            if schedule.status_code not in (200, 201):
                raise NewsletterSendError(
                    f"MailerLite campaign {campaign_id} was created but scheduling "
                    f"failed: HTTP {schedule.status_code} {schedule.text[:500]} — "
                    "the campaign is left as a reviewable draft in the account"
                )

        return {
            "provider": self.provider_name,
            "dry_run": False,
            "campaign_id": str(campaign_id),
            "subject": subject,
            "group_id": self._group_id,
        }


class MailchimpSender(NewsletterSender):
    """Mailchimp Marketing API v3: create -> set content -> pre-flight -> send.

    FOUR calls, not two, and the third one is the point. Mailchimp's
    `GET /campaigns/{id}/send-checklist` reports `is_ready` plus the exact
    reasons it is not -- missing reply_to, missing audience physical
    address, empty content, unsubscribe-link problems. `actions/send` is a
    ONE-SHOT, IRREVERSIBLE call with no undo, so this adapter gates on the
    checklist rather than discovering a misconfiguration by sending a
    broken newsletter to every CFO on the list. The extra round trip is
    the cheapest insurance in this file.

    DATACENTRE PREFIX: Mailchimp's base URL embeds the account's
    datacentre, and the key itself carries it as the suffix after the last
    hyphen (`...-us6` -> `https://us6.api.mailchimp.com/3.0`). Derived
    rather than configured, so there is one fewer setting to get wrong.

    AUTH: `Authorization: Bearer <key>` -- Mailchimp accepts this as well
    as HTTP Basic with any username. An API key is the documented choice
    for a service acting on its OWN account; OAuth is only for
    multi-tenant apps acting on someone else's.

    NOT IMPLEMENTED HERE, deliberately: audience membership. Adding,
    updating and suppressing contacts is the CRM sync's job, not the
    publish path's, and mixing them would let a send accidentally mutate
    the list. This class only ever reads the audience by id.
    """

    provider_name = "mailchimp"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        audience_id: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get(MAILCHIMP_API_KEY_ENV)
        self._audience_id = audience_id or os.environ.get(NEWSLETTER_LIST_ID_ENV)
        self._from_email = from_email or os.environ.get(NEWSLETTER_FROM_EMAIL_ENV)
        self._from_name = from_name or os.environ.get(
            NEWSLETTER_FROM_NAME_ENV, "Canvas Intelligence"
        )
        self._api_base = (api_base or os.environ.get("MAILCHIMP_API_BASE") or "").rstrip("/")

    @staticmethod
    def datacentre_from_key(api_key: str) -> str | None:
        """The suffix after the final hyphen, e.g. 'us6'. None if absent."""
        if "-" not in api_key:
            return None
        suffix = api_key.rsplit("-", 1)[-1].strip()
        return suffix or None

    def base_url(self) -> str:
        if self._api_base:
            return self._api_base
        if not self._api_key:
            raise NewsletterSendError(
                f"cannot derive the Mailchimp datacentre without {MAILCHIMP_API_KEY_ENV}"
            )
        datacentre = self.datacentre_from_key(self._api_key)
        if not datacentre:
            raise NewsletterSendError(
                "Mailchimp API key carries no datacentre suffix (expected a trailing "
                "'-us6'-style segment); set MAILCHIMP_API_BASE explicitly if using OAuth"
            )
        return f"https://{datacentre}.api.mailchimp.com/3.0"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _require_config(self) -> None:
        missing = [
            name
            for name, value in (
                (MAILCHIMP_API_KEY_ENV, self._api_key),
                (NEWSLETTER_LIST_ID_ENV, self._audience_id),
                (NEWSLETTER_FROM_EMAIL_ENV, self._from_email),
            )
            if not value
        ]
        if missing:
            raise NewsletterSendError(
                f"Mailchimp sender is not configured: missing {', '.join(missing)}"
            )

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Mailchimp returns RFC7807-shaped errors. `detail` is the useful
        line and `instance` is the id their support asks for, so surface
        both rather than a raw body dump."""
        try:
            payload = response.json()
        except ValueError:
            return response.text[:400]
        if not isinstance(payload, dict):
            return response.text[:400]
        parts = [str(payload.get("detail") or payload.get("title") or "")]
        for field_error in payload.get("errors") or []:
            if isinstance(field_error, dict):
                parts.append(f"{field_error.get('field')}: {field_error.get('message')}")
        instance = payload.get("instance")
        if instance:
            parts.append(f"(instance {instance})")
        return " ".join(p for p in parts if p)[:600]

    def send(
        self,
        *,
        subject: str,
        html: str,
        dry_run: bool,
        campaign_name: str | None = None,
    ) -> dict[str, Any]:
        if not subject.strip():
            raise NewsletterSendError("newsletter subject is empty")
        if not html.strip():
            raise NewsletterSendError("newsletter body is empty")

        if dry_run:
            return {
                "provider": self.provider_name,
                "dry_run": True,
                "subject": subject,
                "audience_id": self._audience_id,
                "body_bytes": len(html.encode("utf-8")),
            }

        self._require_config()
        base = self.base_url()
        name = campaign_name or subject

        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            create = client.post(
                f"{base}/campaigns",
                headers=self._headers(),
                json={
                    "type": "regular",
                    "recipients": {"list_id": self._audience_id},
                    "settings": {
                        "subject_line": subject,
                        "title": name,
                        "from_name": self._from_name,
                        "reply_to": self._from_email,
                        # Our own template carries the compliant footer, so
                        # Mailchimp's default block would duplicate it. The
                        # *|UNSUB|* tag is still mandatory -- Mailchimp
                        # appends its own if the HTML omits it.
                        "auto_footer": False,
                        "inline_css": True,
                    },
                },
            )
            if create.status_code not in (200, 201):
                raise NewsletterSendError(
                    f"Mailchimp campaign create failed: HTTP {create.status_code} "
                    f"{self._error_detail(create)}"
                )

            campaign_id = (create.json() or {}).get("id")
            if not campaign_id:
                raise NewsletterSendError(
                    "Mailchimp campaign create returned no campaign id; nothing was sent"
                )

            content = client.put(
                f"{base}/campaigns/{campaign_id}/content",
                headers=self._headers(),
                json={"html": html},
            )
            if content.status_code not in (200, 201):
                raise NewsletterSendError(
                    f"Mailchimp campaign {campaign_id} was created but setting content "
                    f"failed: HTTP {content.status_code} {self._error_detail(content)} — "
                    "the campaign is left as a reviewable draft in the account"
                )

            checklist = client.get(
                f"{base}/campaigns/{campaign_id}/send-checklist", headers=self._headers()
            )
            if checklist.status_code != 200:
                raise NewsletterSendError(
                    f"Mailchimp send-checklist for campaign {campaign_id} could not be "
                    f"read: HTTP {checklist.status_code} {self._error_detail(checklist)} — "
                    "refusing to send unchecked"
                )
            checklist_body = checklist.json() or {}
            if not checklist_body.get("is_ready"):
                blockers = "; ".join(
                    f"{item.get('type')}: {item.get('heading')} — {item.get('details')}"
                    for item in (checklist_body.get("items") or [])
                    if item.get("type") == "error"
                )
                raise NewsletterSendError(
                    f"Mailchimp says campaign {campaign_id} is not ready to send "
                    f"({blockers or 'no error detail returned'}) — the campaign is left "
                    "as a reviewable draft in the account and nothing was sent"
                )

            # One-shot, irreversible. 204 No Content on success, empty body.
            sent = client.post(
                f"{base}/campaigns/{campaign_id}/actions/send", headers=self._headers()
            )
            if sent.status_code not in (200, 204):
                raise NewsletterSendError(
                    f"Mailchimp send failed for campaign {campaign_id}: "
                    f"HTTP {sent.status_code} {self._error_detail(sent)}"
                )

        return {
            "provider": self.provider_name,
            "dry_run": False,
            "campaign_id": str(campaign_id),
            "subject": subject,
            "audience_id": self._audience_id,
        }


_PROVIDERS: dict[str, type[NewsletterSender]] = {
    MailchimpSender.provider_name: MailchimpSender,
    MailerLiteSender.provider_name: MailerLiteSender,
}

_PROVIDER_KEY_ENVS: dict[str, str] = {
    MailchimpSender.provider_name: MAILCHIMP_API_KEY_ENV,
    MailerLiteSender.provider_name: MAILERLITE_API_KEY_ENV,
}


def newsletter_provider_name() -> str:
    return os.environ.get(NEWSLETTER_PROVIDER_ENV, DEFAULT_PROVIDER).strip().lower()


def is_newsletter_live_mode() -> bool:
    """True only when the configured provider's credential is resolvable.

    Mirrors analytics_ingest.credentials.is_live_mode's contract: never
    raises, so an unconfigured deploy degrades to 'cannot send' rather
    than to an import-time or startup-time failure.
    """
    key_env = _PROVIDER_KEY_ENVS.get(newsletter_provider_name())
    if key_env is None:
        return False
    return bool(os.environ.get(key_env))


def build_newsletter_sender() -> NewsletterSender:
    """Resolve the configured provider. Unknown provider is an error, not
    a silent fallback to the default -- a typo in NEWSLETTER_ESP_PROVIDER
    must not quietly send through a provider nobody chose."""
    provider = newsletter_provider_name()
    sender_class = _PROVIDERS.get(provider)
    if sender_class is None:
        raise NewsletterSendError(
            f"unknown newsletter ESP provider {provider!r}; "
            f"known providers: {', '.join(sorted(_PROVIDERS))}"
        )
    return sender_class()
