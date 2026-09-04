"""Smoke validator for the autonomy extension. Exit non-zero on any failure.
Checks: schemas parse; every function dir has prompt.md + output.schema.json +
manifest.yaml with consistent ids; no manifest writes to the permission register;
loops parse and reference known functions; autonomy matrix invariants hold;
eval file is valid JSONL.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


# contracts
schemas = {}
for p in (ROOT / "contracts").glob("*.schema.json"):
    try:
        schemas[p.name] = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        err(f"{p.name}: invalid JSON ({e})")
try:
    import jsonschema

    for name, s in schemas.items():
        jsonschema.Draft202012Validator.check_schema(s)
except ImportError:
    print("note: jsonschema not installed; skipping meta-validation")

card_kinds = set(schemas["option-card.schema.json"]["properties"]["kind"]["enum"])

# matrix
matrix = yaml.safe_load((ROOT / "policies" / "autonomy-matrix.yaml").read_text(encoding="utf-8"))
for k in matrix["non_negotiable_kinds"]:
    if k not in card_kinds:
        err(f"autonomy-matrix non_negotiable kind not in contract enum: {k}")
for lvl in (0, 1):
    if matrix["levels"][lvl].get("default_on_timeout_allowed"):
        err(f"level {lvl} must not allow default_on_timeout")
if matrix["approval_budget"]["cards_per_working_day"] > 10:
    err("approval budget above 10/day defeats the purpose")

# functions
known_functions = set(range(1, 113))
fm_re = re.compile(r"^---\n(.*?)\n---\n", re.S)
for d in sorted((ROOT / "functions").iterdir()):
    if not d.is_dir():
        continue
    m = re.match(r"^(\d+)-(.+)$", d.name)
    if not m:
        # Non-numbered utility dirs (functions/_shared, functions/task-worker)
        # predate this extension and are not function packages at all.
        continue
    fid = int(m.group(1))
    known_functions.add(fid)
    # Pre-existing CMOS function packages (built before this extension) use a
    # different file layout (schema.json, skill.md, tools.yaml — no
    # manifest.yaml) and are out of scope for these checks; only the v2
    # ratification/earn-in packages this extension ships declare manifest.yaml.
    if not (d / "manifest.yaml").exists():
        continue
    for req in ("prompt.md", "output.schema.json", "manifest.yaml"):
        if not (d / req).exists():
            err(f"{d.name}: missing {req}")
    prompt = (d / "prompt.md").read_text(encoding="utf-8")
    fm = fm_re.match(prompt)
    if not fm:
        err(f"{d.name}: prompt.md has no front-matter")
    else:
        meta = yaml.safe_load(fm.group(1))
        if meta.get("function_id") != fid or meta.get("slug") != m.group(2):
            err(f"{d.name}: front-matter id/slug mismatch")
        if not all(re.match(r"^H\d{1,2}$", r) for r in meta.get("replaces_register_rows", [])):
            err(f"{d.name}: bad register rows")
    man = yaml.safe_load((d / "manifest.yaml").read_text(encoding="utf-8"))
    if any("permission-register" in w for w in man.get("writes", [])):
        err(f"{d.name}: manifest writes to permission register - forbidden")
    if "docs/permission-register.yaml" not in man.get("never_writes", []):
        err(f"{d.name}: manifest must declare permission register under never_writes")
    try:
        json.loads((d / "output.schema.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        err(f"{d.name}: output.schema.json invalid ({e})")
    if (
        "docs/permission-register.yaml` is read-only" not in prompt
        and "permission-register.yaml` is read-only" not in prompt
    ):
        err(f"{d.name}: prompt lacks the read-only register rule")

# loops
for p in (ROOT / "loops").glob("*.yaml"):
    loop = yaml.safe_load(p.read_text(encoding="utf-8"))
    for t in loop.get("tasks", []):
        f = t.get("function")
        if f is not None and f not in known_functions:
            err(f"{p.name}: task {t.get('id')} references unknown function {f}")
        emits = t.get("emits")
        if emits and emits not in ("option_card", "record"):
            err(f"{p.name}: task {t.get('id')} bad emits {emits}")

# evals
for i, line in enumerate(
    (ROOT / "evals" / "option-quality.jsonl").read_text(encoding="utf-8").splitlines(), 1
):
    if line.strip():
        try:
            row = json.loads(line)
            assert {"eval_id", "rule", "check", "severity"} <= row.keys()
        except Exception as e:  # noqa: BLE001
            err(f"evals line {i}: {e}")

# register doc rows referenced by functions must exist
# PR 0 landed the doc at docs/blueprint/, not docs/, per the blueprint's own
# provenance note ("This document is v2 ... docs/blueprint/agentic-marketing-engine-v2.md").
reg = (ROOT / "docs" / "blueprint" / "agentic-marketing-engine-v2.md").read_text(encoding="utf-8")
reg_rows = set(re.findall(r"^\| (H\d{1,2}) \|", reg, re.M))
for d in (ROOT / "functions").iterdir():
    if d.is_dir() and (d / "manifest.yaml").exists():
        fm = fm_re.match((d / "prompt.md").read_text(encoding="utf-8"))
        meta = yaml.safe_load(fm.group(1)) if fm else {}
        for r in meta.get("replaces_register_rows", []):
            if reg_rows and r not in reg_rows:
                err(f"{d.name}: replaces {r} which is not in blueprint v2 Appendix B")

if errors:
    print("\n".join("FAIL " + e for e in errors))
    sys.exit(1)
n_loops = len(list((ROOT / "loops").glob("*.yaml")))
print(
    f"OK - {len(schemas)} contracts, {len(known_functions) - 112} new functions, "
    f"{n_loops} loops, matrix and evals valid"
)
