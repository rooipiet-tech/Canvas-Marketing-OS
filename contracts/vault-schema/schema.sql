-- Canvas Marketing OS — Vault schema (frozen v1)
--
-- Creates exactly the 9 core Vault tables:
--   signals, opportunity_cards, briefs, assets, campaigns,
--   agent_runs, gate_decisions, costs, consent_register
--
-- Design notes (see contracts/vault-schema/README constraints in domain.md):
--   * assets: version + approval_state + FK to agent_runs (approving identity)
--     + self-referencing predecessor FK, for asset-chain reconstruction.
--   * gate_decisions: not-null FK to agent_runs; append-only (re-decisions
--     are new rows, never UPDATEs of a prior decision).
--   * costs -> agent_runs -> campaigns: unbroken FK chain for per-campaign
--     cost roll-up.
--   * consent_register: lawful_basis (text, not boolean), channel + purpose
--     columns (not a single global opt-in boolean), and revoked_at distinct
--     from the original consent timestamp.
--
-- Idempotent: safe to re-run against a disposable database.

BEGIN;

-- ---------------------------------------------------------------------
-- Enumerated types
-- ---------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE asset_approval_state AS ENUM (
        'draft', 'pending_review', 'approved', 'rejected', 'superseded'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE gate_decision_outcome AS ENUM (
        'approved', 'rejected', 'escalated'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE agent_run_status AS ENUM (
        'pending', 'running', 'succeeded', 'failed', 'cancelled'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------
-- campaigns — top of the roll-up chain
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campaigns (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    status          text NOT NULL DEFAULT 'draft',
    starts_at       timestamptz,
    ends_at         timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- signals — raw inbound market/customer signals
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signals (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL,
    signal_type     text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at     timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- opportunity_cards — scored opportunities derived from signals
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS opportunity_cards (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       uuid REFERENCES signals(id),
    campaign_id     uuid REFERENCES campaigns(id),
    title           text NOT NULL,
    score           numeric,
    status          text NOT NULL DEFAULT 'new',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- briefs — creative/marketing briefs derived from opportunity cards
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS briefs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_card_id uuid REFERENCES opportunity_cards(id),
    campaign_id         uuid REFERENCES campaigns(id),
    title               text NOT NULL,
    body                text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- agent_runs — every agent/tool invocation; FK to campaigns for roll-up
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     uuid NOT NULL REFERENCES campaigns(id),
    agent_name      text NOT NULL,
    status          agent_run_status NOT NULL DEFAULT 'pending',
    input           jsonb,
    output          jsonb,
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- assets — version + approval chain
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS assets (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brief_id            uuid REFERENCES briefs(id),
    campaign_id         uuid REFERENCES campaigns(id),
    asset_type          text NOT NULL,
    version             integer NOT NULL DEFAULT 1,
    approval_state      asset_approval_state NOT NULL DEFAULT 'draft',
    -- Approving/authoring agent identity — FK to agent_runs.
    agent_run_id        uuid NOT NULL REFERENCES agent_runs(id),
    -- Self-referencing predecessor link for asset-chain reconstruction.
    predecessor_asset_id uuid REFERENCES assets(id),
    storage_uri         text,
    content_hash        text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assets_predecessor_not_self CHECK (predecessor_asset_id IS DISTINCT FROM id)
);

-- ---------------------------------------------------------------------
-- gate_decisions — append-only; not-null FK to agent_runs
--
-- Join convention: assets and gate_decisions both FK to agent_runs but
-- have no direct FK to each other. To resolve "which gate decision
-- authoritatively approved this asset version", join assets.agent_run_id
-- to gate_decisions.agent_run_id and take the latest row:
--   SELECT gd.* FROM gate_decisions gd
--   WHERE gd.agent_run_id = <assets.agent_run_id>
--   ORDER BY gd.decided_at DESC LIMIT 1;
-- Because gate_decisions is append-only (re-decisions insert new rows
-- rather than updating a prior one), the most recent decided_at for a
-- given agent_run_id is the authoritative outcome for that run.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gate_decisions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id    uuid NOT NULL REFERENCES agent_runs(id),
    decided_by      text NOT NULL,
    outcome         gate_decision_outcome NOT NULL,
    reason          text,
    decided_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
    -- Append-only by convention: re-decisions insert a new row referencing
    -- the same agent_run_id rather than UPDATEing a prior decision. No
    -- updated_at column is provided, deliberately, to discourage in-place
    -- mutation of a recorded decision.
);

-- ---------------------------------------------------------------------
-- costs — FK to agent_runs; agent_runs -> campaigns completes the
-- unbroken roll-up chain (costs -> agent_runs -> campaigns).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS costs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id    uuid NOT NULL REFERENCES agent_runs(id),
    provider        text NOT NULL,
    unit            text NOT NULL DEFAULT 'usd',
    amount          numeric(18, 6) NOT NULL,
    incurred_at     timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- consent_register — POPIA s11/s69-shaped consent records
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS consent_register (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_subject_ref text NOT NULL,
    -- Lawful basis for processing (POPIA s11) — a real value, not a boolean.
    lawful_basis    text NOT NULL,
    -- Per-channel, per-purpose opt-in — not a single global consent flag.
    channel         text NOT NULL,
    purpose         text NOT NULL,
    consented_at    timestamptz NOT NULL DEFAULT now(),
    -- Revocation is a distinct event/timestamp from the original consent.
    revoked_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consent_register_unique_grant UNIQUE (data_subject_ref, channel, purpose, consented_at)
);

-- ---------------------------------------------------------------------
-- Helpful indexes for the FK chains above
-- ---------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_opportunity_cards_signal_id ON opportunity_cards(signal_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_cards_campaign_id ON opportunity_cards(campaign_id);
CREATE INDEX IF NOT EXISTS idx_briefs_opportunity_card_id ON briefs(opportunity_card_id);
CREATE INDEX IF NOT EXISTS idx_briefs_campaign_id ON briefs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_campaign_id ON agent_runs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_assets_brief_id ON assets(brief_id);
CREATE INDEX IF NOT EXISTS idx_assets_campaign_id ON assets(campaign_id);
CREATE INDEX IF NOT EXISTS idx_assets_agent_run_id ON assets(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_assets_predecessor_asset_id ON assets(predecessor_asset_id);
CREATE INDEX IF NOT EXISTS idx_gate_decisions_agent_run_id ON gate_decisions(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_costs_agent_run_id ON costs(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_consent_register_data_subject_ref ON consent_register(data_subject_ref);

COMMIT;
