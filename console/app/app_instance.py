"""The single shared FastAPI app instance.

Every route module in this package imports THIS exact `app` object and
decorates directly with `@app.get`/`@app.post` — no APIRouter anywhere.
This is load-bearing for CONSOLE-005: its verify command is a literal
`grep -rn "@app\\.(post|put|patch|delete)" console/` across the whole
console tree, which only finds mutating routes correctly if every route in
every file decorates this one `app` object by that exact spelling.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

app = FastAPI(title="console", description="Canvas Marketing OS operator console")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
