"""The Vault smoke failure diagnostics must never print a password.

deploy-pipeline run 88 failed at the Vault smoke test with ca-vault's pool
raising "SASL authentication failed" against ca-pgbouncer. To see whether the
password the vault secret-writer stored equals the one ca-pgbouncer holds,
deploy-infra.yml's failure branch now reads both and prints ONLY a boolean,
the lengths and the non-secret parts of the connection string.

That block handles real credentials in a CI log, so this guard pins the
property rather than trusting the next edit: no `echo`/`printf` argument may
expand a secret variable, except the `::add-mask::` command (the runner
consumes it and masks the value) and the `${#VAR}` length form.

The check is a pure function over script text so it can be shown to fail on a
leaking snippet as well as pass on the real step.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "deploy-infra.yml"
SECRET_VARS = ("WURL", "WPW", "PBPW", "USERPASS", "REST", "ADMIN_PASSWORD")
STEP_PREFIX = "Poll smoke-test job execution"
BEGIN = 'echo "--- ca-pgbouncer revisions'
END = 'echo "--- end of ca-pgbouncer diagnostics ---"'

# Everything an echo/printf command receives, up to the next command separator.
# (Inspecting only the first quoted argument would miss `printf '%s' "$WURL"`.)
_PRINT_ARGS = re.compile(r"\b(?:echo|printf)\b([^;&|\n]*)")


def password_leaks(script: str) -> list[str]:
    """Lines whose echo/printf argument would expand a secret variable."""
    leaks: list[str] = []
    for lineno, line in enumerate(script.splitlines(), 1):
        for m in _PRINT_ARGS.finditer(line):
            arg = m.group(1).strip()
            if arg.lstrip("\"'").startswith("::add-mask::"):
                continue
            # the length form `${#VAR}` is safe; remove it before looking
            arg = re.sub(r"\$\{#\w+\}", "", arg)
            for var in SECRET_VARS:
                if re.search(rf"\$(?:\{{{var}\b|{var}\b)", arg):
                    leaks.append(f"line {lineno}: echo expands ${var}: {line.strip()}")
    return leaks


def _diagnostics_block() -> str:
    d = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in d["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name", "").startswith(STEP_PREFIX):
                run = step["run"]
                assert BEGIN in run and END in run, "pgbouncer diagnostics block missing"
                return run[run.index(BEGIN): run.index(END) + len(END)]
    raise AssertionError(f"no step named {STEP_PREFIX!r} in {WORKFLOW}")


# ---------------------------------------------------------------- real step


def test_real_step_has_the_diagnostics_block_and_it_prints_no_password():
    block = _diagnostics_block()
    assert "password match=" in block
    assert password_leaks(block) == []


def test_real_block_masks_both_passwords_before_printing_anything_derived():
    block = _diagnostics_block()
    mask = block.index("::add-mask::$WPW")
    assert mask < block.index('echo "secret-writer connection string')
    assert "::add-mask::$PBPW" in block


# ------------------------------------------------- non-vacuity: can it fail


def test_check_flags_an_unmasked_password_echo():
    assert password_leaks('echo "password is $WPW"')
    assert password_leaks('echo "pgbouncer password: ${PBPW}"')
    assert password_leaks("printf '%s' \"$WURL\"")


def test_check_allows_masking_lengths_and_booleans():
    ok = "\n".join(
        [
            'if [ -n "$WPW" ]; then echo "::add-mask::$WPW"; fi',
            'echo "password lengths: secret-writer=${#WPW} ca-pgbouncer=${#PBPW}"',
            'if [ "$WPW" = "$PBPW" ]; then echo "password match=true"; fi',
            'echo "user=$WUSER host_port_db=${HOSTPART%%\\?*}"',
        ]
    )
    assert password_leaks(ok) == []
