"""analytics-ingest configuration — environment-driven.

Mirrors services/model-gateway/config.py's contracts_dir() verbatim: a
CONTRACTS_DIR env override takes precedence; otherwise a checkout-relative
fallback is computed (and only computed) inside the function that needs it,
never at import time, so a container image that sets CONTRACTS_DIR never
evaluates the fallback path at all.
"""

from __future__ import annotations

import os
from pathlib import Path


def contracts_dir() -> Path:
    """Directory holding the frozen contract files, honouring CONTRACTS_DIR."""
    override = os.environ.get("CONTRACTS_DIR", "").strip()
    if override:
        return Path(override)
    # This file lives at services/analytics-ingest/analytics_ingest/config.py
    # — one level deeper than model-gateway/config.py (which sits directly
    # under services/model-gateway/), so the checkout-relative walk needs
    # parents[3] (repo root), not parents[2], to land in the same place:
    # parents[0]=analytics_ingest/, [1]=analytics-ingest/, [2]=services/,
    # [3]=<repo root>.
    return Path(__file__).resolve().parents[3] / "analytics" / "contracts"
