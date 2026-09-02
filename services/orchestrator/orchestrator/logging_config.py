"""Structured (JSON-lines) logging for the orchestrator service (AC-032).

Every log record emitted through a logger configured via configure_logging()
prints as a single JSON object with at least `level` and `event` keys,
easing agent-native log parsing (e.g. via `az containerapp logs show`
piped through `jq`) beyond the documented CLI retrieval commands in
docs/accepted-risks.md.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

# Matches the credential segment of a connection string embedded in an
# exception's text, e.g. "postgresql://user:secret@host:5432/db" ->
# "postgresql://***@host:5432/db". Used by sanitize_exception_text (RISK-001)
# so a malformed DATABASE_URL/VAULT_API_URL never echoes a password into logs.
_CREDENTIAL_RE = re.compile(r"://[^@/\s]+@")


def sanitize_exception_text(exc: BaseException) -> str:
    """Returns str(exc) with any embedded connection-string credentials
    (scheme://user:pass@...) redacted. Safe to call on any exception —
    exceptions with no such pattern pass through unchanged.
    """
    return _CREDENTIAL_RE.sub("://***@", str(exc))


# Matches one run of letters, or one run of digits. Used by
# structural_skeleton() below.
_LETTERS_RE = re.compile(r"[^\W\d_]+")
_DIGITS_RE = re.compile(r"\d+")


def structural_skeleton(text: str, limit: int = 1000) -> str:
    """Returns `text` with every letter replaced by `a`/`A` and every digit
    by `0`, preserving punctuation, whitespace and case pattern.

    This exists so free-form model output can be logged for root-causing a
    parse failure without any possibility of carrying personal information
    into the log. It deliberately does NOT pattern-match for PII:
    contracts/model-gateway/redaction-rules.yaml says in its own header that
    pattern matching is "inherently best-effort and structurally
    incomplete", so a redactor built on those patterns would inherit that
    incompleteness. Replacing every alphanumeric character removes the
    question — a name, an email, a phone number and an ID number all come
    out as `Aaaaa Aaaaa`, `aaaa.aaaaa@aaaaaaa.aa.aa`, `+00 00 000 0000` and
    `0000000000000` regardless of whether any rule anticipated their shape.

    What survives is exactly what diagnosing a JSON parse failure needs: the
    delimiters, the punctuation, the code fences, the whitespace, and where
    in the structure the text stops.

        >>> structural_skeleton('Sure! {"name": "Thabo Nkosi"}')
        'Aaaa! {"aaaa": "Aaaaa Aaaaa"}'
    """

    def _mask_letters(m: re.Match[str]) -> str:
        return "".join("A" if c.isupper() else "a" for c in m.group(0))

    masked = _LETTERS_RE.sub(_mask_letters, text[:limit])
    return _DIGITS_RE.sub(lambda m: "0" * len(m.group(0)), masked)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "extra_fields", None)
        fields: dict[str, Any] = dict(extra) if isinstance(extra, dict) else {}
        # Each log line is a single JSON object (json.dumps below) that
        # always carries "level" and "event" keys, plus "logger" and any
        # caller-supplied extra_fields — this is what makes log output
        # agent-parseable one-record-per-line.
        return json.dumps(
            {
                "level": record.levelname,
                "event": record.getMessage(),
                "logger": record.name,
                **fields,
            }
        )


_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("orchestrator")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"orchestrator.{name}")


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={"extra_fields": fields})
