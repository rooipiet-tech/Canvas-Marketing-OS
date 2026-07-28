"""The single shared FastAPI app instance.

Every route module in this package imports THIS exact `app` object and
decorates it directly (GET routes and the one POST route alike) — no
APIRouter anywhere. This is load-bearing for CONSOLE-005: its grep-based
verify command across the whole console tree only finds mutating routes
correctly if every route in every file decorates this one `app` object by
that exact spelling (deliberately not spelled out literally in this
docstring — the literal decorator substring would itself be a
false-positive match for that same grep).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

app = FastAPI(title="console", description="Canvas Marketing OS operator console")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
