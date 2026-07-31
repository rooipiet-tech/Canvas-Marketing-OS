"""Synthetic-trace test fixtures for telemetry-lib (GOAL-001, Wave-1 scope).

Per the GOAL's own Wave-1 constraint, no other service is instrumented
this session — this package's synthetic span tree is the only trace data
this build produces, used both by telemetry-lib's own unit test and by
deploy-console.yml's gated live smoke job (see .github/workflows/
deploy-console.yml) to prove Application Insights ingestion end-to-end.
"""
