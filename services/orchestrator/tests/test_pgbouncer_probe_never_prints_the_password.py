"""The ca-pgbouncer probe must never print the password, and must never decide
the smoke job's exit code.

infra/modules/vault/pgbouncer-probe.py runs inside caj-vault-smoke-test after the
pytest suite (deploy-pipeline run 89: SASL authentication failed through
ca-pgbouncer although the stored and live passwords match). Its output goes to a
CI log, so this pins the properties instead of trusting the next edit:

1. the probe is valid Python;
2. no print() argument expands the password (or the connection string) except
   through `_redact(...)`, the exception-text scrubber;
3. smoke-test-job.bicep loads the probe, runs it after pytest, and exits with
   pytest's code (`exit $rc`), so a probe problem can never fail, or hide a
   failure of, the real suite.

Each check is a pure function over text so it can be shown to fail as well as
pass.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

VAULT_INFRA = Path(__file__).resolve().parents[3] / "infra" / "modules" / "vault"
PROBE = VAULT_INFRA / "pgbouncer-probe.py"
JOB = VAULT_INFRA / "smoke-test-job.bicep"

SECRET_NAMES = {"password", "parts", "url", "dsn", "database_url"}


def _names_outside_redact(node: ast.AST) -> set[str]:
    """Secret-looking Names reachable from `node`, not inside a _redact() call."""
    found: set[str] = set()

    def walk(n: ast.AST) -> None:
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_redact"
        ):
            return  # the scrubber's own arguments are allowed
        if isinstance(n, ast.Name) and n.id in SECRET_NAMES:
            found.add(n.id)
        for child in ast.iter_child_nodes(n):
            walk(child)

    walk(node)
    return found


def password_leaks(source: str) -> list[str]:
    leaks: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            for arg in node.args:
                for name in sorted(_names_outside_redact(arg)):
                    # `len(password)` is a length, not the value
                    if _only_inside_len(arg, name):
                        continue
                    leaks.append(f"line {node.lineno}: print expands {name!r}")
    return leaks


def _only_inside_len(tree: ast.AST, name: str) -> bool:
    """True when every use of `name` under `tree` is `len(name)`."""
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == name]
    guarded = {
        id(n.args[0])
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "len"
        and n.args
        and isinstance(n.args[0], ast.Name)
        and n.args[0].id == name
    }
    return bool(uses) and all(id(u) in guarded for u in uses)


def job_violations(bicep: str) -> list[str]:
    problems: list[str] = []
    if "loadTextContent('pgbouncer-probe.py')" not in bicep:
        problems.append("smoke-test-job.bicep does not load pgbouncer-probe.py")
    cmd = re.search(r"'(pip install[^\n]*)'", bicep)
    if not cmd:
        problems.append("could not find the container command line")
        return problems
    line = cmd.group(1)
    if 'python -c "$PGBOUNCER_PROBE"' not in line:
        problems.append("the command never runs the probe")
    if "pytest" in line and "PGBOUNCER_PROBE" in line:
        if line.index("pytest") > line.index("PGBOUNCER_PROBE"):
            problems.append("the probe runs before pytest")
    if "rc=$?" not in line or "exit $rc" not in line:
        problems.append("the command does not preserve pytest's exit code")
    return problems


# ---------------------------------------------------------------- real files


def test_probe_is_valid_python():
    ast.parse(PROBE.read_text(encoding="utf-8"))


def test_probe_prints_no_password():
    assert password_leaks(PROBE.read_text(encoding="utf-8")) == []


def test_probe_scrubs_exception_text_and_prints_only_a_length():
    src = PROBE.read_text(encoding="utf-8")
    assert "_redact(str(exc), password)" in src
    assert "password length={len(password)}" in src


def test_job_runs_the_probe_after_pytest_and_keeps_pytests_exit_code():
    assert job_violations(JOB.read_text(encoding="utf-8")) == []


# ------------------------------------------------- non-vacuity: can it fail


def test_leak_check_flags_a_printed_password():
    assert password_leaks('print(f"pw={password}")')
    assert password_leaks('print("dsn", url)')
    assert password_leaks('print(f"{parts}")')
    assert password_leaks('print(f"x: {str(exc)} {password}")')


def test_leak_check_allows_redacted_text_and_lengths():
    ok = (
        'print(f"FAIL {_redact(str(exc), password)}")\n'
        'print(f"password length={len(password)}")\n'
        'print("user", user)\n'
    )
    assert password_leaks(ok) == []


def test_job_check_flags_the_old_command_and_a_swallowed_exit_code():
    old = "'pip install -r requirements-test.txt && pytest tests/ -v'"
    assert any("never runs the probe" in p for p in job_violations(old))
    swallowed = (
        "loadTextContent('pgbouncer-probe.py')\n"
        "'pip install -r r.txt && { pytest tests/ -v; python -c \"$PGBOUNCER_PROBE\"; }'"
    )
    assert any("exit code" in p for p in job_violations(swallowed))
    first = (
        "loadTextContent('pgbouncer-probe.py')\n"
        "'pip install -r r.txt && { python -c \"$PGBOUNCER_PROBE\"; "
        "pytest tests/; rc=$?; exit $rc; }'"
    )
    assert any("before pytest" in p for p in job_violations(first))
