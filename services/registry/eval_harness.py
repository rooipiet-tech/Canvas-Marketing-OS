#!/usr/bin/env python3
"""Run golden eval sets for function-definition packages.

Run standalone (from repo root):

    python services/registry/eval_harness.py --all
    python services/registry/eval_harness.py --function functions/42-linkedin-post-writer
    python services/registry/eval_harness.py --all --live

Default mode is **mocked**: every completion goes through
`gateway_client.build_mock_client`, which installs an in-process transport.
There is no socket, no DNS lookup and no way to reach the real gateway host,
and the harness asserts zero live calls *per rubric check type* (not merely
in aggregate) before reporting success.

`--live` is wired but deferred (C1/C8). With `ANTHROPIC_API_KEY` unset it
prints an explicit `SKIPPED` marker per function and exits 0 — it never
silently no-ops and never attempts a real call. With a key set it takes a
materially different code path, prints a `LIVE ATTEMPT` marker and issues
exactly one POST per function; whether that call succeeds is irrelevant to
the exit code, because CI never has both a key and Azure access in this
scope.

Base URL resolution (L-0025) is `CMOS_GATEWAY_BASE_URL` env var first, then
a dynamic `az containerapp show` lookup of the gateway's real live FQDN
(`gateway_client.resolve_live_gateway_fqdn`) — never a hardcoded/guessed
hostname. If neither resolves, the function reports `LIVE ATTEMPT SKIPPED`
and exits 0 rather than attempting against a fabricated host.

Each package's `tool_check.py` is loaded via importlib under a module name
derived from its **full resolved path**, so two packages loaded in one
process (run-all mode) — including the regression fixture, which is a copy of
a real package — can never collide. See `load_tool_check_module` for why the
module is registered in `sys.modules` under that unique name rather than kept
out of it entirely.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType

import telemetry_wiring
from checks import GENERIC_KINDS, apply_generic_check
from common import (
    discover_function_packages,
    eval_task_files,
    fail,
    rel_or_str,
)
from gateway_client import (
    GatewayContractError,
    build_completion_request,
    build_live_client,
    build_mock_client,
    resolve_live_gateway_fqdn,
)

LIVE_API_KEY_ENV = "ANTHROPIC_API_KEY"
LIVE_BASE_URL_ENV = "CMOS_GATEWAY_BASE_URL"

CHECK_TYPES = ("tool_check", "llm_judged")

GRADER_SYSTEM_PROMPT = (
    "You are a grading function. You are given one rubric criterion, its discrete "
    "pass/fail condition, and a candidate output. Reply with a JSON object "
    '{"verdict": "PASS"} or {"verdict": "FAIL", "detail": "<why>"} and nothing else. '
    "Apply the condition literally. Do not reward or penalise anything the condition "
    "does not name."
)


def synthetic_module_name(prefix: str, module_path: Path) -> str:
    """Collision-free module name derived from a module's FULL resolved path.

    Package directory names alone are not enough: run-all mode loads several
    `tool_check.py` files in one process, and the regression fixture is a
    copy of a real package, so only the full path distinguishes them.
    """
    digest = hashlib.sha256(str(module_path).encode("utf-8")).hexdigest()[:16]
    slug = "".join(char if char.isalnum() else "_" for char in module_path.parent.name)
    return f"{prefix}__{slug}__{digest}"


def load_tool_check_module(package_dir: Path) -> ModuleType:
    """Load a package's tool_check.py by path under a collision-free name.

    The module IS registered in `sys.modules`, deliberately: `dataclasses`,
    `typing.get_type_hints` and pickling all resolve a class's module through
    `sys.modules`, and a package helper that defines a dataclass (function
    02's `permission_check.Clearance` does) fails to import without it. The
    collision risk that registration would normally create is removed by
    keying the name on the full resolved path rather than the directory name.
    """
    module_path = (package_dir / "tool_check.py").resolve()
    if not module_path.is_file():
        fail(f"{rel_or_str(package_dir)} has no tool_check.py — cannot grade its eval tasks")

    module_name = synthetic_module_name("cmos_tool_check", module_path)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        fail(f"cannot build an import spec for {rel_or_str(module_path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surface any package-side import error
        sys.modules.pop(module_name, None)
        fail(f"{rel_or_str(module_path)} failed to import: {exc!r}")
    return module


def load_tasks(package_dir: Path) -> list[dict]:
    tasks = []
    for task_path in eval_task_files(package_dir):
        try:
            tasks.append(json.loads(task_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            fail(f"{rel_or_str(task_path)} is not valid JSON: {exc}")
    if not tasks:
        fail(f"{rel_or_str(package_dir)}/evals contains no golden eval tasks")
    return tasks


def dispatch_check(task: dict, entry: dict, output: str, module: ModuleType) -> tuple[bool, str]:
    """Grade one rubric entry: generic kinds centrally, everything else in-package."""
    check = entry.get("check") or {}
    kind = check.get("kind")
    if kind in GENERIC_KINDS:
        return apply_generic_check(check, output)
    if not hasattr(module, "run_check"):
        return False, f"check kind {kind!r} is not generic and the package has no run_check()"
    return module.run_check(task, entry, output)


class TaskResult:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.failures: list[str] = []

    @property
    def passed(self) -> bool:
        return not self.failures


def run_task_mocked(
    task: dict,
    prompt_text: str,
    module: ModuleType,
    live_calls_by_check_type: dict[str, int],
) -> TaskResult:
    """Run one golden task end-to-end against the mocked gateway."""
    result = TaskResult(str(task.get("id", "<no id>")))
    state: dict[str, str] = {"output": ""}

    def responder(payload: dict) -> str:
        envelope_text = payload["messages"][-1]["content"]
        try:
            envelope = json.loads(envelope_text)
        except json.JSONDecodeError:
            envelope = {}
        if envelope.get("mode") == "grade":
            entry = task["rubric"][int(envelope["rubric_index"])]
            passed, detail = dispatch_check(task, entry, state["output"], module)
            return json.dumps({"verdict": "PASS" if passed else "FAIL", "detail": detail})
        return module.mock_completion(task, prompt_text)

    with build_mock_client(responder) as client:
        generate_payload = build_completion_request(
            system_prompt=prompt_text,
            user_content=json.dumps({"mode": "generate", "input": task.get("input", {})}),
            agent_run_id=str(uuid.uuid5(uuid.NAMESPACE_OID, result.task_id)),
        )
        try:
            response = client.complete(generate_payload, purpose="generate")
        except GatewayContractError as exc:
            result.failures.append(f"gateway contract error on generation: {exc}")
            return result
        state["output"] = response["content"]

        for index, entry in enumerate(task.get("rubric", [])):
            check_type = str(entry.get("check_type", "tool_check"))
            before_live = client.counters.live_calls

            if check_type == "llm_judged":
                grade_payload = build_completion_request(
                    system_prompt=GRADER_SYSTEM_PROMPT,
                    user_content=json.dumps(
                        {
                            "mode": "grade",
                            "rubric_index": index,
                            "criterion": entry.get("criterion"),
                            "condition": entry.get("condition"),
                        }
                    ),
                    agent_run_id=str(
                        uuid.uuid5(uuid.NAMESPACE_OID, f"{result.task_id}-grade-{index}")
                    ),
                )
                grade_response = client.complete(grade_payload, purpose="grade")
                grade = json.loads(grade_response["content"])
                passed = grade.get("verdict") == "PASS"
                detail = grade.get("detail", "")
            else:
                passed, detail = dispatch_check(task, entry, state["output"], module)

            live_calls_by_check_type[check_type] += client.counters.live_calls - before_live

            if not passed:
                result.failures.append(
                    f"[{check_type}] {entry.get('criterion')}: {entry.get('condition')} "
                    f"-> {detail}"
                )

    return result


def run_function_mocked(package_dir: Path) -> tuple[int, int, list[str]]:
    """Run one package's whole golden eval set.

    Returns (passed, total, failing_task_ids). Failing task ids are collected
    on this single pass — the harness never re-runs a package to work out
    what failed.
    """
    prompt_text = (package_dir / "prompt.md").read_text(encoding="utf-8")
    module = load_tool_check_module(package_dir)
    tasks = load_tasks(package_dir)

    live_calls_by_check_type = {check_type: 0 for check_type in CHECK_TYPES}
    results = [
        run_task_mocked(task, prompt_text, module, live_calls_by_check_type) for task in tasks
    ]

    function_id = package_dir.name
    print(f"\n{function_id} ({len(tasks)} golden task(s), mocked gateway)")
    for result in results:
        if result.passed:
            print(f"  PASS  {result.task_id}")
        else:
            print(f"  FAIL  {result.task_id}")
            for failure in result.failures:
                print(f"          {failure}")

    for check_type, live_calls in sorted(live_calls_by_check_type.items()):
        if live_calls != 0:
            fail(
                f"{function_id}: {live_calls} real network call(s) made while scoring "
                f"{check_type} rubric entries — default mode must never contact a real host"
            )
    print(
        "  zero real network calls while scoring "
        + ", ".join(f"{check_type}={count}" for check_type, count in sorted(
            live_calls_by_check_type.items()
        ))
    )

    passed = sum(1 for result in results if result.passed)
    total = len(results)
    failing_ids = [f"{function_id}/{result.task_id}" for result in results if not result.passed]
    print(f"  {function_id}: {passed}/{total} tasks passed")
    return passed, total, failing_ids


def run_function_live(package_dir: Path) -> bool:
    """Wired-but-deferred live path. Skips cleanly with no credential."""
    function_id = package_dir.name
    api_key = os.environ.get(LIVE_API_KEY_ENV, "").strip()

    if not api_key:
        print(
            f"SKIPPED {function_id} — --live requested but {LIVE_API_KEY_ENV} is unset; "
            "no live gateway call was attempted"
        )
        return True

    # Base URL resolution order (L-0025): an explicit override first (used by
    # test_live_path.py's local stub), then a dynamic lookup of the gateway's
    # real live FQDN. Never a hardcoded/guessed hostname — the one this build
    # used to fall back to (model-gateway.internal.cmos.dev) turned out to
    # have no matching DNS zone at all.
    base_url = os.environ.get(LIVE_BASE_URL_ENV, "").strip()
    resolution = "explicit CMOS_GATEWAY_BASE_URL" if base_url else None
    if not base_url:
        base_url = resolve_live_gateway_fqdn() or ""
        resolution = "az containerapp show (live FQDN)" if base_url else None
    if not base_url:
        print(
            f"LIVE ATTEMPT SKIPPED {function_id} — {LIVE_API_KEY_ENV} is set but no gateway "
            f"base URL could be resolved: set {LIVE_BASE_URL_ENV} explicitly, or ensure "
            f"`az containerapp show` can reach a live gateway (L-0025: never guess a hostname)"
        )
        return True

    tasks = load_tasks(package_dir)
    prompt_text = (package_dir / "prompt.md").read_text(encoding="utf-8")
    task = tasks[0]

    print(
        f"LIVE ATTEMPT {function_id} -> POST {base_url}/v1/completions (task {task['id']}, "
        f"base URL via {resolution})"
    )
    payload = build_completion_request(
        system_prompt=prompt_text,
        user_content=json.dumps({"mode": "generate", "input": task.get("input", {})}),
        agent_run_id=str(uuid.uuid5(uuid.NAMESPACE_OID, str(task["id"]))),
    )
    # AC-03/AC-04 telemetry adoption (plan step 16): a standalone span per
    # live attempt -- this is a CLI-invoked client call with no upstream
    # request to join, so there's no incoming traceparent to extract, only
    # this call's own outgoing one. Full DE-5 propagation onto the outbound
    # POST itself would require extending registry/gateway_client.py's
    # GatewayClient to accept per-call headers (out of scope for this
    # already-"wired but deferred" C1/C8 path) -- the span still records
    # this attempt with the 5 required attributes either way.
    with telemetry_wiring.emit_live_eval_span(
        function_id=function_id, task_ref=str(task["id"]), model=payload["model"]
    ):
        try:
            with build_live_client(base_url, api_key) as client:
                response = client.complete(payload, purpose="generate")
            print(f"LIVE RESULT {function_id} -> {len(response['content'])} char(s) returned")
        except Exception as exc:  # noqa: BLE001 - the attempt is the point, not the outcome
            # Per C1/C8 no live gateway is reachable from this scope. The
            # attempt itself is the observable behaviour being asserted
            # (AC-10); its success is explicitly out of scope for this build.
            print(f"LIVE RESULT {function_id} -> attempt failed ({type(exc).__name__}: {exc})")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run golden eval sets for function packages.")
    parser.add_argument(
        "--function",
        type=Path,
        action="append",
        default=[],
        help="run only this package directory's own tasks (repeatable)",
    )
    parser.add_argument("--all", action="store_true", help="run every package under functions/")
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "take the wired-but-deferred live path; SKIPPED per function when "
            f"{LIVE_API_KEY_ENV} is unset"
        ),
    )
    args = parser.parse_args()

    package_dirs: list[Path] = list(args.function)
    if args.all or not package_dirs:
        package_dirs.extend(discover_function_packages())
    if not package_dirs:
        fail("no function-definition packages to run")

    for package_dir in package_dirs:
        if not package_dir.is_dir():
            fail(f"{rel_or_str(package_dir)} is not a directory")

    if args.live:
        for package_dir in package_dirs:
            run_function_live(package_dir)
        print(f"\nlive mode: {len(package_dirs)} function(s) processed")
        print("PASS")
        return

    failing_ids: list[str] = []
    total_passed = 0
    total_tasks = 0
    for package_dir in package_dirs:
        passed, total, failures = run_function_mocked(package_dir)
        total_passed += passed
        total_tasks += total
        failing_ids.extend(failures)

    print(
        f"\ntotal: {total_passed}/{total_tasks} tasks passed "
        f"across {len(package_dirs)} function(s)"
    )

    if failing_ids:
        fail(f"{len(failing_ids)} golden eval task(s) failed: " + ", ".join(failing_ids))

    print("PASS")


if __name__ == "__main__":
    main()
