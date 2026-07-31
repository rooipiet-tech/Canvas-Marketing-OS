"""Shared HTML-or-JSON rendering helper + the canonical Decimal JSON encoder.

Every JSON-returning route in the console reuses `decimal_json_encoder`
unmodified — this is the single canonical numeric serialization shared by
the cost-ledger route and any future route touching money, which is what
makes GOAL-002's byte-for-byte comparison against an independently
computed harness aggregation achievable by construction, not luck.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates


def decimal_json_encoder(value: Decimal) -> str:
    """Fixed-format, pinned-quantize string for a Decimal — never a Python
    float, never scientific notation."""
    return format(value, "f")


def render_or_json(
    request: Request,
    template_name: str,
    data: dict[str, Any],
    templates: Jinja2Templates,
) -> Any:
    """Render `template_name` with `data` as HTML, unless the request's
    Accept header asks for application/json, in which case return the same
    `data` as a JSON response (excluding the `request` context key)."""
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        json_data = {key: value for key, value in data.items() if key != "request"}
        encoded = jsonable_encoder(json_data, custom_encoder={Decimal: decimal_json_encoder})
        return JSONResponse(content=encoded)

    return templates.TemplateResponse(request, template_name, data)
