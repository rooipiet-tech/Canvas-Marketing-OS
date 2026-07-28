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
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "extra_fields", None)
        fields: dict[str, Any] = dict(extra) if isinstance(extra, dict) else {}
        # AC-032: json.dumps(...) below always includes "level" and "event" keys.
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
