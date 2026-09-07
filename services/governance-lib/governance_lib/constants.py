"""Cross-service constants that must stay byte-identical everywhere they
are consulted (TD-08).

AGENT_NAME_LOOP_PROOF is read by two places that share no other library:

  * services/orchestrator/orchestrator/dispatch.py tags every real
    model-calling handler reached by the S8 proof circuit's
    `params.proof_circuit: true` tasks with this as the Vault
    `agent_run.agent_name`.
  * services/publisher/app/vault_lookup.py resolves an asset's
    `agent_run.agent_name` and, if it equals this value, forces
    `dry_run=True` regardless of `PUBLISHER_DRY_RUN` — the structural
    guarantee that the proof circuit can exercise the real
    signal -> brief -> draft -> QA -> approval path without ever
    actually publishing.

A byte-for-byte mismatch here would let a stray loop-proof agent_run slip
past whichever side's check keys off the literal string — either a
real publish escaping the poison pill, or a real content wrongly forced
into dry-run. Both the orchestrator (Docker-built; installed as an
editable sibling package, see services/orchestrator/Dockerfile) and
Publisher (BUNDLE-deployed; this file is embedded as source via
BUNDLE_MANIFEST.txt + infra/main.bicep) now import this single constant
instead of each declaring their own literal.
"""

from __future__ import annotations

AGENT_NAME_LOOP_PROOF = "loop-proof-circuit"
