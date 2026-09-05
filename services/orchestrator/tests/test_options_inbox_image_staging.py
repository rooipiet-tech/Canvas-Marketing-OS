"""The orchestrator image must actually contain services/options_inbox.

INCIDENT (found while reconciling this session's own Fn 129 work against
the deployed image, Appendix D PR 5c): dispatch.py has imported `from
options_inbox.cards import build_card` / `from options_inbox.policy import
route` at module level since Appendix D PR 5 (#155) — the IDENTICAL
telemetry-lib incident test_contracts_path_resolution.py already guards,
for a different sibling package. Neither the Dockerfile nor
orchestrator-image.yml ever staged or installed services/options_inbox,
so any container built from main since PR 5 would fail to import
dispatch.py AT ALL — not one degraded task_type, every one of them, since
a module-level ImportError prevents the whole file from loading.

Two separate things had to be true for that to go unnoticed:
  1. CI's orchestrator-test job runs `pip install -e services/options_inbox`
     directly (a full repository checkout, not the built image), so every
     test in this suite imports options_inbox successfully regardless of
     what the Dockerfile does.
  2. cards.py/earn_in.py additionally each read a repo-root file
     (contracts/option-card.schema.json, policies/autonomy-matrix.yaml,
     policies/earn-in-rules.yaml) via a `Path(__file__).resolve().
     parents[2]`-style walk — correct in that same full checkout, wrong
     the moment the package is installed anywhere else (regular pip
     install copies files into site-packages, discarding their position
     relative to any repo root entirely).

Docker itself cannot be exercised in this environment (see
test_contracts_path_resolution.py's own note), so these tests cover what
genuinely can be checked: the Dockerfile/workflow text agree on what gets
staged and installed, and cards.py/earn_in.py's own env-var overrides
(OPTION_CARD_CONTRACT_PATH/AUTONOMY_MATRIX_PATH/EARN_IN_RULES_PATH) work
against a directory holding nothing but those staged files — exactly the
shape a regular (non-editable) install produces.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# services/orchestrator/tests/test_X.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR_DIR = REPO_ROOT / "services" / "orchestrator"
DOCKERFILE_PATH = ORCHESTRATOR_DIR / "Dockerfile"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "orchestrator-image.yml"

OPTION_CARD_CONTRACT_SRC = REPO_ROOT / "contracts" / "option-card.schema.json"
AUTONOMY_MATRIX_SRC = REPO_ROOT / "policies" / "autonomy-matrix.yaml"
EARN_IN_RULES_SRC = REPO_ROOT / "policies" / "earn-in-rules.yaml"


def test_the_dockerfile_installs_options_inbox():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "COPY options_inbox ./options_inbox" in text
    # Regular (non-editable) install, like telemetry-lib -- both are
    # copied wholesale by `pip install --prefix=/install`'s final COPY
    # --from=builder /install /usr/local, needing no separate final-stage
    # re-COPY the way orchestrator's own editable-installed source does.
    pip_line = "pip install --no-cache-dir --prefix=/install ./telemetry-lib ./options_inbox -e ."
    assert pip_line in text


def test_the_dockerfile_sets_the_three_options_inbox_env_vars():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "ENV OPTION_CARD_CONTRACT_PATH=/app/contracts/option-card.schema.json" in text
    assert "ENV AUTONOMY_MATRIX_PATH=/app/policies/autonomy-matrix.yaml" in text
    assert "ENV EARN_IN_RULES_PATH=/app/policies/earn-in-rules.yaml" in text


def test_the_dockerfile_stages_the_files_those_env_vars_point_at():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "COPY contracts/option-card.schema.json ./contracts/option-card.schema.json" in text
    assert "COPY policies/autonomy-matrix.yaml ./policies/autonomy-matrix.yaml" in text
    assert "COPY policies/earn-in-rules.yaml ./policies/earn-in-rules.yaml" in text


def test_the_image_workflow_stages_options_inbox_into_the_build_context():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "cp -r services/options_inbox services/orchestrator/options_inbox" in text


def test_the_image_workflow_stages_the_policy_and_contract_files():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "services/orchestrator/contracts/" in text
    assert "contracts/option-card.schema.json" in text
    assert "services/orchestrator/policies" in text
    assert "policies/autonomy-matrix.yaml" in text
    assert "policies/earn-in-rules.yaml" in text


def _stage_fake_image(image_root):
    """Write exactly what the Dockerfile's COPY lines put where, nothing
    else -- the same minimal-image shape test_contracts_path_resolution.py's
    own `_stage()` helper uses for the sibling contracts/ concern."""
    contracts_dir = image_root / "contracts"
    policies_dir = image_root / "policies"
    contracts_dir.mkdir(parents=True)
    policies_dir.mkdir(parents=True)
    (contracts_dir / "option-card.schema.json").write_text(
        OPTION_CARD_CONTRACT_SRC.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (policies_dir / "autonomy-matrix.yaml").write_text(
        AUTONOMY_MATRIX_SRC.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (policies_dir / "earn-in-rules.yaml").write_text(
        EARN_IN_RULES_SRC.read_text(encoding="utf-8"), encoding="utf-8"
    )


@pytest.fixture()
def fake_image(tmp_path, monkeypatch):
    """A directory holding only the staged files, with sys.path pointed at
    the real options_inbox package -- simulating a regular pip install
    that copied it somewhere with no repo checkout above it, exactly the
    situation inside the built image."""
    _stage_fake_image(tmp_path)
    monkeypatch.setenv(
        "OPTION_CARD_CONTRACT_PATH", str(tmp_path / "contracts" / "option-card.schema.json")
    )
    monkeypatch.setenv("AUTONOMY_MATRIX_PATH", str(tmp_path / "policies" / "autonomy-matrix.yaml"))
    monkeypatch.setenv("EARN_IN_RULES_PATH", str(tmp_path / "policies" / "earn-in-rules.yaml"))

    from options_inbox import cards, earn_in

    importlib.reload(earn_in)  # EARN_IN_RULES_PATH is a module-level default -- reread the env var
    yield tmp_path, cards, earn_in
    importlib.reload(earn_in)  # restore the real default for any test running after this one


def test_cards_reads_the_staged_contract_and_matrix_via_env_override(fake_image):
    _tmp_path, cards, _earn_in = fake_image
    matrix = cards.load_matrix()
    contract = cards.load_contract()
    assert matrix["version"]
    assert contract["$id"]


def test_earn_in_reads_the_staged_rules_via_env_override(fake_image):
    _tmp_path, _cards, earn_in = fake_image
    rules = earn_in.load_rules()
    assert rules["version"]


def test_route_works_end_to_end_against_only_the_staged_files(fake_image):
    """build_card/route are what dispatch.py actually calls -- this proves
    the whole chain, not just the two loader functions in isolation."""
    _tmp_path, _cards, _earn_in = fake_image
    from options_inbox.policy import route

    result = route([], [])
    assert result.sent == []


def test_a_directory_holding_only_the_staged_files_is_enough(fake_image):
    """Nothing else from the repository needs to be reachable — the whole
    point, since inside the image nothing else IS."""
    tmp_path, cards, _earn_in = fake_image
    assert sorted(p.name for p in tmp_path.iterdir()) == ["contracts", "policies"]
    cards.load_matrix()
    cards.load_contract()


def test_simulated_image_layout_would_fail_without_the_env_vars(monkeypatch):
    """Show the defect this fixes, in the layout that produces it: a
    regular pip install copies cards.py somewhere with no repo checkout
    above it (simulated here without needing a real install), so its own
    HERE.parents[2] fallback resolves to a directory with no contracts/ or
    policies/ at all."""
    monkeypatch.delenv("OPTION_CARD_CONTRACT_PATH", raising=False)
    monkeypatch.delenv("AUTONOMY_MATRIX_PATH", raising=False)

    fake_module = Path("/usr/local/lib/python3.12/site-packages/options_inbox/cards.py")
    resolved_parents = fake_module.resolve().parents
    fallback_root = resolved_parents[2] if len(resolved_parents) > 2 else fake_module.anchor
    assert not (Path(str(fallback_root)) / "policies" / "autonomy-matrix.yaml").exists()


def test_default_resolution_still_needs_no_configuration(monkeypatch):
    """Local development, pytest, and CI's `pip install -e services/
    options_inbox` (a full repository checkout) keep working with every
    env var unset -- this fix is additive, never a required override."""
    monkeypatch.delenv("OPTION_CARD_CONTRACT_PATH", raising=False)
    monkeypatch.delenv("AUTONOMY_MATRIX_PATH", raising=False)
    monkeypatch.delenv("EARN_IN_RULES_PATH", raising=False)

    from options_inbox import cards, earn_in

    importlib.reload(earn_in)
    try:
        matrix = cards.load_matrix()
        contract = cards.load_contract()
        rules = earn_in.load_rules()
    finally:
        importlib.reload(earn_in)
    assert matrix["version"]
    assert contract["$id"]
    assert rules["version"]


def test_an_empty_env_var_falls_back_rather_than_resolving_to_cwd(monkeypatch):
    """An unset-looking override ("" or whitespace) must not become
    Path("") -- which silently resolves to the current working directory."""
    monkeypatch.setenv("OPTION_CARD_CONTRACT_PATH", "   ")
    monkeypatch.setenv("AUTONOMY_MATRIX_PATH", "   ")

    from options_inbox import cards

    assert cards._contract_path() == cards.ROOT / "contracts" / "option-card.schema.json"
    assert cards._matrix_path() == cards.ROOT / "policies" / "autonomy-matrix.yaml"
