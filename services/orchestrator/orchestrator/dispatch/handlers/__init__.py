"""Per-task-type handler modules, one file per dispatch-table section.

Split out of orchestrator/dispatch.py (TD-17) as a pure move -- see
dispatch/__init__.py's module docstring and dispatch/table.py for how
these are assembled into DISPATCH_TABLE.
"""

from __future__ import annotations
