# Canvas Marketing OS — orchestrator service image
#
# Pinned patch tag (never :latest for the base image, F7). Non-root
# runtime user: dependencies are installed as root (needed for
# system-level installs), then USER app is set BEFORE the final CMD, so
# the process itself never runs as root. COPY only — no ADD from a URL.
#
# Multi-stage build (RISK-002): requirements-test.txt (pytest/
# pytest-asyncio/freezegun) and the tests/ directory are only needed to run
# this repo's test suite — that happens in CI directly on the runner (see
# .github/workflows/ci.yml's orchestrator-test job), never inside this
# image. The builder stage installs only the orchestrator package itself
# into a self-contained --prefix; that prefix plus the application source
# are copied into the final runtime stage, so pytest and friends are never
# even installed anywhere this image's layers.

FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

COPY pyproject.toml ./
COPY main.py ./
COPY orchestrator ./orchestrator
COPY loops ./loops
COPY migrations ./migrations
COPY scripts ./scripts

# INCIDENT (2026-08-01, live — caj-loop-e2e-smoke #8 attempt 2): every
# dispatched task failed with `ModuleNotFoundError: No module named
# 'telemetry_lib'` the instant it reached dispatch.py's top-level `from
# telemetry_lib import set_span_attribute` (dispatch.py line 40) — /health
# and GET /runs/{task_ref} kept responding 200 because neither route
# imports dispatch.py, so the run just silently never advanced past 0/7
# stages terminal instead of crashing loudly. Root cause: telemetry-lib is
# a sibling package (services/telemetry-lib), not published to any index —
# this image never installed it, despite pyproject.toml's own header
# comment describing a "separate `pip install -e services/telemetry-lib`
# step" that mirrors console/Dockerfile's convention; that step was never
# actually added here. .github/workflows/orchestrator-image.yml's "Stage
# telemetry-lib into the build context" step copies the sibling package in
# — the same mechanism already used for contracts/, functions/, and docs/
# below, all of which live outside this image's services/orchestrator
# build context for the identical reason.
COPY telemetry-lib ./telemetry-lib

# INCIDENT (Appendix D PR 5c, found while reconciling this session's own
# Fn 129 work against the deployed image): dispatch.py has imported
# `from options_inbox.cards import build_card` / `from options_inbox.policy
# import route` at module level since Appendix D PR 5 (#155-158) -- the
# IDENTICAL telemetry-lib incident above, for a different sibling package.
# This image never installed services/options_inbox either, so dispatch.py
# would fail to import at all the instant any container built from main
# tried to load it -- not a degraded task, a dead module, breaking every
# task_type in DISPATCH_TABLE, not only the new ones. Same fix, same
# reasoning as telemetry-lib immediately above: staged into this build
# context first (orchestrator-image.yml's "Stage options_inbox into the
# build context" step) rather than widening this workflow's build context
# to the repo root. No existing precedent elsewhere in the repo -- console/
# Dockerfile never uses options_inbox at all.
COPY options_inbox ./options_inbox

RUN pip install --no-cache-dir --prefix=/install ./telemetry-lib ./options_inbox -e .

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN useradd --create-home --shell /bin/bash app

COPY --from=builder /install /usr/local
COPY --from=builder /app/main.py ./
COPY --from=builder /app/orchestrator ./orchestrator
COPY --from=builder /app/loops ./loops
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/scripts ./scripts

# INCIDENT (2026-07-31): confirmed live via ca-orchestrator's own startup
# logs (`loop_load_failed ... No such file or directory:
# '/contracts/orchestrator/loop-definition.schema.json'`) and
# caj-orchestrator-smoke-test crashing before it could publish anything.
# The frozen contract files loop_loader.py (every shipped loop, validated
# on startup and by the smoke test's own prediction path) and producer.py
# (the service-bus/orchestrator envelope schemas every outgoing message is
# validated against before publish, AC-026) read at RUNTIME live at the
# repository root, in contracts/ — outside this image's build context
# (services/orchestrator), which Docker's COPY cannot reach.
# .github/workflows/orchestrator-image.yml's "Stage the frozen contracts
# into the build context" step copies them into ./contracts/ here,
# immediately before `docker build` runs. Written out explicitly (not
# folded into a wildcard COPY) so a build without that staging step FAILS
# LOUDLY at build time instead of producing an image that only breaks on
# its first loop load / publish.
COPY contracts/orchestrator/loop-definition.schema.json ./contracts/orchestrator/loop-definition.schema.json
COPY contracts/orchestrator/heartbeat-event.schema.json ./contracts/orchestrator/heartbeat-event.schema.json
COPY contracts/orchestrator/dead-letter-alert.schema.json ./contracts/orchestrator/dead-letter-alert.schema.json
COPY contracts/service-bus/task-envelope.schema.json ./contracts/service-bus/task-envelope.schema.json
# Same incident class as the options_inbox note above, its second half:
# services/options_inbox/cards.py's load_contract()/load_matrix() read
# these two repo-root files at runtime through their own OPTION_CARD_
# CONTRACT_PATH / AUTONOMY_MATRIX_PATH env overrides (cards.py's own
# header has the full writeup) -- set below, pointing at these staged
# copies, exactly like CONTRACTS_DIR/FUNCTIONS_DIR already do.
COPY contracts/option-card.schema.json ./contracts/option-card.schema.json
COPY policies/autonomy-matrix.yaml ./policies/autonomy-matrix.yaml
COPY policies/earn-in-rules.yaml ./policies/earn-in-rules.yaml
# Appendix D PR 5c: dispatch.py's own Fn 129 handlers (web_reach_
# allowlist_review_handler, web_reach_allowlist_monthly_review_handler)
# read these four via config.policies_dir() at runtime -- POLICIES_DIR
# below points that resolver at this same staged directory.
COPY policies/allowlist-rule.yaml ./policies/allowlist-rule.yaml
COPY policies/allowlist-deny.yaml ./policies/allowlist-deny.yaml
COPY policies/discovery-budget.yaml ./policies/discovery-budget.yaml
COPY policies/standing-permissions-seed.yaml ./policies/standing-permissions-seed.yaml

# Points config.contracts_dir() at the staged copies above. Without it the
# app falls back to walking parent directories from its own source file
# looking for a repository checkout, which does not exist inside this
# image — see orchestrator/config.py's contracts_dir() header for the full
# incident writeup.
ENV CONTRACTS_DIR=/app/contracts
ENV OPTION_CARD_CONTRACT_PATH=/app/contracts/option-card.schema.json
ENV AUTONOMY_MATRIX_PATH=/app/policies/autonomy-matrix.yaml
ENV EARN_IN_RULES_PATH=/app/policies/earn-in-rules.yaml
# Points config.policies_dir() at the staged copies above -- same
# CONTRACTS_DIR/FUNCTIONS_DIR pattern, see config.py's policies_dir()
# header for the full incident writeup this mirrors.
ENV POLICIES_DIR=/app/policies

# SAME incident class, caught while reconciling this session's own
# dispatch.py against the fix above (plan step 25's rebase reconciliation
# pass, test_contracts_path_resolution.py's module-level-parent-walk sweep
# test): dispatch.py's ingest-signals/qa-review/draft-content handlers read
# a prompt.md, a permission_check.py, and a scan-profiles.yaml from
# functions/ at RUNTIME — also outside this image's build context, for the
# identical reason contracts/ is. .github/workflows/orchestrator-image.yml's
# "Stage the frozen functions into the build context" step copies just the
# files these handlers actually read into ./functions/ here, immediately
# before `docker build` runs — never the rest of any function package
# (evals/, tool_check.py, skill.md, etc. stay out, same RISK-002
# minimal-image intent as contracts/ above).
#
# EXTENDED (6 Aug 2026, weekly-content-loop dispatch handlers): the same
# gap this comment already warns about bit again immediately -- PR #84
# added 8 new _read_prompt() call sites (functions 26, 39, 41, 43, 45, 46,
# 47, 52) plus a brand-new function package (48-fact-check-verdict) and
# none of the 9 were added here, so the built image would have shipped
# without them and every one of those handlers (everything except the
# deterministic plan-content-monday_handler) would have raised
# FileNotFoundError the first time weekly-content-loop actually dispatched
# a task -- caught before that image ever reached ca-orchestrator, not
# after. Only prompt.md is staged for each: dispatch.py's _read_prompt()
# never reads anything else from these 9 packages (no scan profile,
# no permission_check.py -- client-naming clearance stays centralised in
# function 02's copy, already staged below).
COPY functions/09-market-intelligence-director/prompt.md ./functions/09-market-intelligence-director/prompt.md
COPY functions/09-market-intelligence-director/schema.json ./functions/09-market-intelligence-director/schema.json
# functions/_shared/scan-profiles.yaml replaced function 09's own
# fetch_sources.yaml (F-SCAN-PROFILE-SINGLETON): the file describes what
# TWELVE scanners read, so it no longer lives inside one package. Same
# runtime-read, same staging requirement -- ingest_signals_handler reads
# it on every dispatch, so an image without it raises FileNotFoundError
# on the first daily scan.
COPY functions/_shared/scan-profiles.yaml ./functions/_shared/scan-profiles.yaml
# score-signals reads this on every daily run; _recent_scored_signals
# reads it on every Monday plan. Absent, both degrade to the built-in
# defaults rather than failing -- which is exactly the silent drift an
# explicit COPY (and its test) exists to prevent.
COPY functions/_shared/scoring-policy.yaml ./functions/_shared/scoring-policy.yaml
# Read at runtime by probe_sources_handler on every source-discovery run
# (weekly). Same staging requirement, same failure mode if missed.
COPY functions/_shared/source-candidates.yaml ./functions/_shared/source-candidates.yaml
# Appendix D PR 5b: _bootstrap_candidate_pool (dispatch.py) reads this on
# every source-lifecycle-loop.yaml discovery/retire dispatch. Same
# staging requirement, same FileNotFoundError failure mode if missed.
COPY functions/_shared/source-candidates.bootstrap.yaml ./functions/_shared/source-candidates.bootstrap.yaml
# function 17 (Source Scout) — read by propose_sources_handler on every
# weekly source-discovery run: prompt.md via _read_prompt, schema.json by
# the input and output validators.
COPY functions/17-source-scout/prompt.md ./functions/17-source-scout/prompt.md
COPY functions/17-source-scout/schema.json ./functions/17-source-scout/schema.json
COPY functions/42-linkedin-post-writer/prompt.md ./functions/42-linkedin-post-writer/prompt.md
COPY functions/02-brand-steward-qa/prompt.md ./functions/02-brand-steward-qa/prompt.md
COPY functions/02-brand-steward-qa/permission_check.py ./functions/02-brand-steward-qa/permission_check.py
COPY functions/26-client-advocacy-harvester/prompt.md ./functions/26-client-advocacy-harvester/prompt.md
# Function 25 (Competitive Response Strategist) -- read at runtime by
# competitive_response_strategize_handler, the step the eleven fan-out
# scanners feed. It was staged nowhere at all until that handler
# existed, because nothing had ever called it.
COPY functions/25-competitive-response-strategist/prompt.md ./functions/25-competitive-response-strategist/prompt.md
COPY functions/39-insight-to-story-editor/prompt.md ./functions/39-insight-to-story-editor/prompt.md
COPY functions/41-research-brief-writer/prompt.md ./functions/41-research-brief-writer/prompt.md
COPY functions/43-executive-ghostwriter/prompt.md ./functions/43-executive-ghostwriter/prompt.md
COPY functions/45-carousel-post-writer/prompt.md ./functions/45-carousel-post-writer/prompt.md
COPY functions/46-newsletter-writer/prompt.md ./functions/46-newsletter-writer/prompt.md
COPY functions/47-case-study-writer/prompt.md ./functions/47-case-study-writer/prompt.md
COPY functions/48-fact-check-verdict/prompt.md ./functions/48-fact-check-verdict/prompt.md
COPY functions/52-content-repurposer/prompt.md ./functions/52-content-repurposer/prompt.md
# Appendix D PR 5: compose_options_handler reads 116's prompt.md on every
# thursday-compose-options-* dispatch (weekly-content-loop.yaml).
# route_digest_handler makes no model call at all (functions/117-
# approval-inbox-router/schema.json's own header explains why), so it
# never calls _read_prompt -- staged anyway, for the same completeness
# reasoning the block above already gives functions 43/47.
COPY functions/116-options-composer/prompt.md ./functions/116-options-composer/prompt.md
COPY functions/117-approval-inbox-router/prompt.md ./functions/117-approval-inbox-router/prompt.md
# Appendix D PR 5b: every source-lifecycle-loop.yaml handler reads
# 128's prompt.md via _read_prompt on every model-calling dispatch.
COPY functions/128-source-discovery-lifecycle/prompt.md ./functions/128-source-discovery-lifecycle/prompt.md
# Appendix D PR 5c: Fn 129's own handlers make no model call (deterministic
# rule evaluation, see dispatch.py's own module-section docstring), so
# nothing here ever calls _read_prompt(FUNCTION_ID_129) -- staged anyway
# for the same completeness reasoning Fn 117's own prompt.md already gets
# above, and because test_function_assets_are_staged.py's generic sweep
# (every FUNCTION_ID_* naming a real functions/ package) does not
# distinguish "staged for a real read" from "staged for completeness".
COPY functions/129-web-reach-governor/prompt.md ./functions/129-web-reach-governor/prompt.md
# W1 + Appendix D PR 6/7: Fn 126 makes no model call (like Fn 129,
# deterministic arithmetic over GET /decision-history) -- staged anyway
# for the same completeness reasoning. Fn 127's eval_generator_handler
# DOES read its own prompt.md via _read_prompt on every real dispatch.
COPY functions/126-decision-quality-evaluator/prompt.md ./functions/126-decision-quality-evaluator/prompt.md
COPY functions/127-eval-generator/prompt.md ./functions/127-eval-generator/prompt.md
# Appendix D PR 8: both handlers make a real model call, reading their
# own prompt.md via _read_prompt on every dispatch.
COPY functions/113-expertise-corpus-miner/prompt.md ./functions/113-expertise-corpus-miner/prompt.md
COPY functions/114-executive-voice-model/prompt.md ./functions/114-executive-voice-model/prompt.md
COPY functions/115-position-proposer/prompt.md ./functions/115-position-proposer/prompt.md

# The "only prompt.md is staged" note above stopped being true the moment
# schema.json became the wire format rather than documentation.
# _validate_function_input / _validate_function_output load a package's
# own schema.json at runtime, from functions/, so every package the
# orchestrator validates against needs its schema in the image or the
# handler raises on the first real dispatch -- the identical failure this
# block's own header records for the missing prompts, one file type
# later.
#
# Function 41's has been missing since output validation was added to it:
# staged now rather than discovered on a deploy. Functions 43 and 47 are
# here for completeness even though their handlers currently decline
# before any validation runs (no executive configured, no cleared
# engagement) -- the day either is configured, the schema must already be
# present.
COPY functions/25-competitive-response-strategist/schema.json ./functions/25-competitive-response-strategist/schema.json
COPY functions/02-brand-steward-qa/schema.json ./functions/02-brand-steward-qa/schema.json
COPY functions/26-client-advocacy-harvester/schema.json ./functions/26-client-advocacy-harvester/schema.json
COPY functions/42-linkedin-post-writer/schema.json ./functions/42-linkedin-post-writer/schema.json
COPY functions/39-insight-to-story-editor/schema.json ./functions/39-insight-to-story-editor/schema.json
COPY functions/41-research-brief-writer/schema.json ./functions/41-research-brief-writer/schema.json
COPY functions/43-executive-ghostwriter/schema.json ./functions/43-executive-ghostwriter/schema.json
COPY functions/45-carousel-post-writer/schema.json ./functions/45-carousel-post-writer/schema.json
COPY functions/46-newsletter-writer/schema.json ./functions/46-newsletter-writer/schema.json
COPY functions/47-case-study-writer/schema.json ./functions/47-case-study-writer/schema.json
COPY functions/48-fact-check-verdict/schema.json ./functions/48-fact-check-verdict/schema.json
COPY functions/52-content-repurposer/schema.json ./functions/52-content-repurposer/schema.json
# Appendix D PR 5: compose_options_handler's _validate_function_input/
# _validate_function_output calls (function 116) and route_digest_
# handler's _validate_function_output call (function 117, validating its
# own RoutingResult-shaped output against schema.json even though it
# makes no model call) both need these staged.
COPY functions/116-options-composer/schema.json ./functions/116-options-composer/schema.json
COPY functions/117-approval-inbox-router/schema.json ./functions/117-approval-inbox-router/schema.json
# Appendix D PR 5b: every source-lifecycle-loop.yaml handler validates
# its input/output against 128's schema.json.
COPY functions/128-source-discovery-lifecycle/schema.json ./functions/128-source-discovery-lifecycle/schema.json
# Appendix D PR 5c: web_reach_allowlist_review_handler's own
# _validate_function_output(FUNCTION_ID_129, ...) call reads this at
# runtime -- unlike the prompt above, this one IS actually read.
COPY functions/129-web-reach-governor/schema.json ./functions/129-web-reach-governor/schema.json
# W1 + Appendix D PR 6/7: both handlers validate input/output against
# these at runtime (_validate_function_output for Fn 126, both for Fn 127).
COPY functions/126-decision-quality-evaluator/schema.json ./functions/126-decision-quality-evaluator/schema.json
COPY functions/127-eval-generator/schema.json ./functions/127-eval-generator/schema.json
COPY functions/113-expertise-corpus-miner/schema.json ./functions/113-expertise-corpus-miner/schema.json
COPY functions/114-executive-voice-model/schema.json ./functions/114-executive-voice-model/schema.json
COPY functions/115-position-proposer/schema.json ./functions/115-position-proposer/schema.json

# The eleven S10 fan-out scanners (F1). Registered in DISPATCH_TABLE
# from one factory, each reading its OWN package's prompt.md through
# _read_prompt() at dispatch time -- so every one of them needs staging
# here for exactly the reason the block above records: an image missing
# one raises FileNotFoundError the first time that scanner's profile
# has sources and the task actually runs. Only prompt.md is staged;
# schema.json is read from functions/ too (runtime output validation),
# so it is staged alongside.
COPY functions/10-competitor-discovery-scanner/prompt.md ./functions/10-competitor-discovery-scanner/prompt.md
COPY functions/10-competitor-discovery-scanner/schema.json ./functions/10-competitor-discovery-scanner/schema.json
COPY functions/11-competitor-change-monitor/prompt.md ./functions/11-competitor-change-monitor/prompt.md
COPY functions/11-competitor-change-monitor/schema.json ./functions/11-competitor-change-monitor/schema.json
COPY functions/12-competitive-positioning-analyst/prompt.md ./functions/12-competitive-positioning-analyst/prompt.md
COPY functions/12-competitive-positioning-analyst/schema.json ./functions/12-competitive-positioning-analyst/schema.json
COPY functions/13-competitor-content-performance-scout/prompt.md ./functions/13-competitor-content-performance-scout/prompt.md
COPY functions/13-competitor-content-performance-scout/schema.json ./functions/13-competitor-content-performance-scout/schema.json
COPY functions/16-microsoft-fabric-ecosystem-scout/prompt.md ./functions/16-microsoft-fabric-ecosystem-scout/prompt.md
COPY functions/16-microsoft-fabric-ecosystem-scout/schema.json ./functions/16-microsoft-fabric-ecosystem-scout/schema.json
COPY functions/18-01-vertical-intel-logistics-fleet/prompt.md ./functions/18-01-vertical-intel-logistics-fleet/prompt.md
COPY functions/18-01-vertical-intel-logistics-fleet/schema.json ./functions/18-01-vertical-intel-logistics-fleet/schema.json
COPY functions/18-02-vertical-intel-mining-industrial/prompt.md ./functions/18-02-vertical-intel-mining-industrial/prompt.md
COPY functions/18-02-vertical-intel-mining-industrial/schema.json ./functions/18-02-vertical-intel-mining-industrial/schema.json
COPY functions/18-03-vertical-intel-manufacturing/prompt.md ./functions/18-03-vertical-intel-manufacturing/prompt.md
COPY functions/18-03-vertical-intel-manufacturing/schema.json ./functions/18-03-vertical-intel-manufacturing/schema.json
COPY functions/18-04-vertical-intel-construction/prompt.md ./functions/18-04-vertical-intel-construction/prompt.md
COPY functions/18-04-vertical-intel-construction/schema.json ./functions/18-04-vertical-intel-construction/schema.json
COPY functions/18-05-vertical-intel-fmcg-beverage/prompt.md ./functions/18-05-vertical-intel-fmcg-beverage/prompt.md
COPY functions/18-05-vertical-intel-fmcg-beverage/schema.json ./functions/18-05-vertical-intel-fmcg-beverage/schema.json
COPY functions/18-06-vertical-intel-financial-services/prompt.md ./functions/18-06-vertical-intel-financial-services/prompt.md
COPY functions/18-06-vertical-intel-financial-services/schema.json ./functions/18-06-vertical-intel-financial-services/schema.json

# Points config.functions_dir() at the staged copies above -- same
# rationale as CONTRACTS_DIR; see orchestrator/config.py's functions_dir()
# header for the full incident writeup.
ENV FUNCTIONS_DIR=/app/functions

# SAME incident class again (output-reviewer F2, major, same fix pattern
# as CONTRACTS_DIR/FUNCTIONS_DIR above): permission_check.py's
# find_repo_root() walks up looking for pyproject.toml to locate
# docs/permission-register.yaml -- there is no repository checkout inside
# this image, so that walk would raise RuntimeError the first time
# client_references is ever non-empty (dormant today only because
# dispatch.py hardcodes client_references=[]). Stage just the one file
# permission_check.py actually reads, never the rest of docs/.
COPY docs/permission-register.yaml ./docs/permission-register.yaml
# Appendix D PR 8: expertise_corpus_mine_handler (Fn 113) reads this at
# runtime through its own POSITIONING_MD_PATH env override (dispatch.py's
# _positioning_md_path(), mirroring permission_check.py's register_path()
# pattern exactly).
COPY docs/positioning.md ./docs/positioning.md

# Points permission_check.py's register_path() at the staged copy above --
# see functions/02-brand-steward-qa/permission_check.py's register_path()
# header for the full incident writeup.
ENV PERMISSION_REGISTER_PATH=/app/docs/permission-register.yaml
ENV POSITIONING_MD_PATH=/app/docs/positioning.md

# caj-orchestrator-smoke-test (infra/modules/orchestrator/smoke-test-job.bicep)
# runs `python -m orchestrator.smoke_test` from THIS SAME image — it
# compares the live app's /status output against this one static golden
# fixture. Shipping just this one JSON file (never the rest of tests/ — no
# pytest, no test framework code, no test dependencies; see the
# multi-stage build note above, RISK-002) keeps that note's intent intact
# while still giving the smoke test something to compare against inside
# the image that actually runs it. Already inside this image's build
# context (services/orchestrator) — no separate staging step needed,
# unlike contracts/ above.
COPY tests/golden/daily-signal-loop.expected.json ./tests/golden/daily-signal-loop.expected.json

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
