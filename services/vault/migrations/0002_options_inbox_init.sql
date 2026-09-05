-- Canvas Marketing OS — options_inbox schema (Appendix D PR 1)
--
-- Three tables backing the ratification model (blueprint v2 §C1-C4):
-- option_cards, approval_decisions, standing_permissions. Lands in the
-- `public` schema, alongside agent_runs and campaigns — this is core
-- business data, not vault-service-internal bookkeeping, so it does not
-- follow the vault_internal (0001) pattern.
--
-- NOT added to contracts/vault-schema/schema.sql. That file is one of the
-- 7 frozen-v1 contracts scripts/validate_contracts.py guards by sha256;
-- CLAUDE.md's hard rule 1 is explicit that a frozen file is never mutated,
-- additive or not — a genuinely new contract lands under a new namespace,
-- never by editing the published file. These three tables are v2+
-- additions with their own contract of record already: option_cards'
-- `card` column stores exactly the shape contracts/option-card.schema.json
-- defines; approval_decisions' shape mirrors
-- contracts/approval-decision.schema.json; standing_permissions mirrors
-- contracts/standing-permission.schema.json. This migration is the
-- storage for those contracts, not a fourth copy of them.
--
-- option_cards.agent_run_id references a REAL agent_runs row — the PR
-- #105 lesson every option-card-emitting handler must respect (see
-- services/orchestrator/orchestrator/dispatch.py's own comment on
-- schedule_social_buffer_handler for the incident this guards against).
--
-- Idempotent: every statement uses IF NOT EXISTS guards, mirroring 0001's
-- own convention, so this file can be safely re-applied against an
-- already-migrated database (verified in CI by applying it twice in a
-- row, same as 0001).
--
-- Applied through the same in-VNet Container Apps Job mechanism as
-- caj-vault-migrate, with the identical base64-encoding fix for Container
-- Apps' handling of "$$" in secret values applied defensively (this file
-- contains no dollar-quoted PL/pgSQL blocks, but the CI check still
-- verifies the round-trip, matching 0001's own practice).

BEGIN;

CREATE TABLE IF NOT EXISTS option_cards (
    card_id                 uuid PRIMARY KEY,
    kind                    text NOT NULL,
    autonomy_level          smallint NOT NULL,
    risk_tier               text NOT NULL,
    agent_run_id            uuid REFERENCES agent_runs(id),
    produced_by_function    smallint NOT NULL,
    card                    jsonb NOT NULL,
    created_at              timestamptz NOT NULL,
    expires_at              timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_option_cards_agent_run_id ON option_cards(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_option_cards_kind ON option_cards(kind);
CREATE INDEX IF NOT EXISTS idx_option_cards_expires_at ON option_cards(expires_at);
CREATE INDEX IF NOT EXISTS idx_option_cards_produced_by_function ON option_cards(produced_by_function);

CREATE TABLE IF NOT EXISTS approval_decisions (
    id                  bigserial PRIMARY KEY,
    card_id             uuid NOT NULL REFERENCES option_cards(card_id),
    outcome             text NOT NULL,
    chosen_option_id    char(1),
    was_recommended     boolean,
    rejection_code      text,
    decided_by          text NOT NULL,
    decided_at          timestamptz NOT NULL,
    latency_seconds     integer,
    signature           text NOT NULL,
    CONSTRAINT approval_decisions_one_per_card UNIQUE (card_id)
    -- One decision per card, replay-rejected exactly like gate_decisions'
    -- own append-only convention (contracts/vault-schema/schema.sql's own
    -- comment on that table) — except here a card is genuinely single-use
    -- rather than append-only, since C2's own contract says "One decision
    -- per card."
);

CREATE INDEX IF NOT EXISTS idx_approval_decisions_decided_at ON approval_decisions(decided_at);
CREATE INDEX IF NOT EXISTS idx_approval_decisions_outcome ON approval_decisions(outcome);

CREATE TABLE IF NOT EXISTS standing_permissions (
    permission_id   text PRIMARY KEY,
    status          text NOT NULL,
    rule            jsonb NOT NULL,
    granted_by      text,
    granted_at      timestamptz,
    review_by       date
);

CREATE INDEX IF NOT EXISTS idx_standing_permissions_status ON standing_permissions(status);
CREATE INDEX IF NOT EXISTS idx_standing_permissions_review_by ON standing_permissions(review_by);

COMMIT;
