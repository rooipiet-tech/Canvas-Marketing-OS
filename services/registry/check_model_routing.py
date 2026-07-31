#!/usr/bin/env python3
"""Assert every model id this registry references is a live routing.yaml entry.

Run standalone (from repo root):

    python services/registry/check_model_routing.py

Per L-0026: a config value naming an external-system identifier (here, a
model-gateway *logical* model id) can be silently wrong forever if nothing
ever checks it against the source of truth — a mocked/stubbed gateway
accepts any string, so the eval harness's default mode proves routing
*logic* works but nothing about whether a referenced id still exists.

This script reads `services/model-gateway/policy/routing.yaml` (owned by
the model-gateway build; read-only from here, not in this service's
touch-scope to write) and confirms every logical model id this registry's
own tooling and function packages reference is a key in that file's
`models` mapping. It does not (and cannot, from here) re-verify
routing.yaml's own literal `provider_model` values against a live
`GET /v1/models` call — that check belongs to the gateway that owns the
file (see routing.yaml's own header comment).

If routing.yaml is absent from this checkout, the check skips cleanly
rather than failing — it is not this service's file to require to exist.
"""

from __future__ import annotations

import sys

from common import REPO_ROOT, discover_function_packages, fail, load_yaml, rel_or_str, warn
from gateway_client import DEFAULT_MODEL

ROUTING_YAML_PATH = REPO_ROOT / "services" / "model-gateway" / "policy" / "routing.yaml"


def referenced_model_ids() -> dict[str, list[str]]:
    """Every logical model id this registry's own code/config references.

    Maps model id -> the source location(s) it came from, so a failure can
    name exactly where a stale id was found rather than just the id itself.
    """
    sources: dict[str, list[str]] = {}

    def add(model_id: str, source: str) -> None:
        sources.setdefault(model_id, []).append(source)

    add(DEFAULT_MODEL, "services/registry/gateway_client.py:DEFAULT_MODEL")

    for package_dir in discover_function_packages():
        tools_path = package_dir / "tools.yaml"
        if not tools_path.is_file():
            continue
        data = load_yaml(tools_path)
        model_id = data.get("model") if isinstance(data, dict) else None
        if model_id:
            add(str(model_id), f"{rel_or_str(tools_path)}:model")

    return sources


def main() -> None:
    if not ROUTING_YAML_PATH.is_file():
        warn(
            f"{rel_or_str(ROUTING_YAML_PATH)} not found in this checkout — skipping "
            "model-id alignment check (not this service's file to require)"
        )
        print("SKIPPED (routing.yaml absent)")
        return

    routing = load_yaml(ROUTING_YAML_PATH) or {}
    valid_ids = set((routing.get("models") or {}).keys())
    if not valid_ids:
        fail(f"{rel_or_str(ROUTING_YAML_PATH)} has no models entries to validate against")

    sources = referenced_model_ids()
    stale = {
        model_id: locations for model_id, locations in sources.items() if model_id not in valid_ids
    }
    if stale:
        for model_id, locations in sorted(stale.items()):
            for location in locations:
                print(
                    f"FAIL: {location} references model id {model_id!r}, which is not a key "
                    f"in {rel_or_str(ROUTING_YAML_PATH)}'s models mapping "
                    f"(known ids: {sorted(valid_ids)})",
                    file=sys.stderr,
                )
        fail(f"{len(stale)} stale/unknown model id(s) referenced — see FAIL lines above")

    for model_id, locations in sorted(sources.items()):
        print(f"ok  {model_id} -> {rel_or_str(ROUTING_YAML_PATH)} ({', '.join(locations)})")
    print("PASS")


if __name__ == "__main__":
    main()
