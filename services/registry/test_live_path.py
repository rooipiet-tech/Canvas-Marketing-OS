#!/usr/bin/env python3
"""Prove the --live path skips cleanly without a key and attempts with one.

Run standalone (from repo root):

    python services/registry/test_live_path.py

Case A (AC-09) — the case that must hold in THIS environment: with
`ANTHROPIC_API_KEY` unset, `eval_harness.py --live` exits 0 and prints an
explicit `SKIPPED <function id>` marker per function. It must not silently
no-op, and it must not attempt a real call.

Case B (AC-10): with a dummy key set and the local stub gateway as the base
URL, the harness takes a materially different code path — it prints a
`LIVE ATTEMPT` marker and the stub intercepts exactly one
`POST /v1/completions` per function. Whether the call succeeds is irrelevant;
the attempt is the observable behaviour.

Exits 0 with PASS, or 1 with `FAIL: <reason>`.
"""

from __future__ import annotations

import os
import subprocess
import sys

from common import REGISTRY_DIR, REPO_ROOT, discover_function_packages, fail, rel_or_str
from eval_harness import LIVE_API_KEY_ENV, LIVE_BASE_URL_ENV
from stub_gateway import run_stub_gateway

HARNESS = REGISTRY_DIR / "eval_harness.py"


def run_harness(package_dirs, env_overrides):
    env = dict(os.environ)
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    args = []
    for package_dir in package_dirs:
        args.extend(["--function", rel_or_str(package_dir)])
    return subprocess.run(
        [sys.executable, str(HARNESS), *args, "--live"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def main() -> None:
    packages = discover_function_packages()
    expect(len(packages) >= 3, f"expected at least 3 function packages, found {len(packages)}")

    # --- Case A: no key -> clean SKIPPED per function, exit 0.
    for package_dir in packages:
        result = run_harness(
            [package_dir], {LIVE_API_KEY_ENV: None, LIVE_BASE_URL_ENV: None}
        )
        expect(
            result.returncode == 0,
            f"--live with no key must exit 0 for {package_dir.name}, got "
            f"{result.returncode}: {result.stderr.strip()}",
        )
        expect(
            f"SKIPPED {package_dir.name}" in result.stdout,
            f"--live with no key must print 'SKIPPED {package_dir.name}'; "
            f"stdout was:\n{result.stdout}",
        )
        expect(
            "LIVE ATTEMPT" not in result.stdout,
            f"--live with no key must not attempt a call for {package_dir.name}",
        )

    # --- Case B: dummy key + local stub -> LIVE ATTEMPT and exactly one POST each.
    with run_stub_gateway() as (base_url, log):
        result = run_harness(
            packages,
            {LIVE_API_KEY_ENV: "dummy-key-for-stub-only", LIVE_BASE_URL_ENV: base_url},
        )
        expect(
            result.returncode == 0,
            f"--live against the stub must exit 0, got {result.returncode}: "
            f"{result.stderr.strip()}",
        )
        expect(
            "SKIPPED" not in result.stdout,
            "with a key set, no function may report SKIPPED — that is the skip path",
        )
        for package_dir in packages:
            expect(
                f"LIVE ATTEMPT {package_dir.name}" in result.stdout,
                f"expected a LIVE ATTEMPT marker for {package_dir.name}; "
                f"stdout was:\n{result.stdout}",
            )
        intercepted = log.count("POST", "/v1/completions")
        expect(
            intercepted == len(packages),
            f"stub gateway intercepted {intercepted} POST(s) to /v1/completions, "
            f"expected exactly {len(packages)} (one per function)",
        )
        for entry in log.requests:
            payload = entry["payload"] or {}
            missing = [
                field for field in ("model", "messages", "agent_run_id") if field not in payload
            ]
            expect(
                not missing,
                f"a live request reached the stub missing required field(s) {missing} — "
                "contract validation must happen before the transport",
            )

    print(f"case A  {LIVE_API_KEY_ENV} unset -> SKIPPED per function, exit 0, no call attempted")
    print(
        f"case B  dummy key + local stub -> LIVE ATTEMPT per function, "
        f"{len(packages)} POST(s) to /v1/completions intercepted"
    )
    print("PASS")


if __name__ == "__main__":
    main()
