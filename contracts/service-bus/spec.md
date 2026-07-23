# Service Bus contract — v1

## Namespace and queues

Canvas Marketing OS uses a single Azure Service Bus **Standard** SKU
namespace (dev tier — see `docs/accepted-risks.md`) with two queues:

- **`task`** — work items dispatched to agent/task workers (task-worker
  function, model-gateway service, etc.). Envelope shape:
  `contracts/service-bus/task-envelope.schema.json`.
- **`event`** — fan-out notifications of state changes (e.g. asset
  approved, gate decision recorded, campaign status changed) for
  downstream subscribers.

## Metadata only — no client data in envelopes

**Every message placed on either queue carries task metadata only.**
Envelopes reference domain rows by id (`agent_run_id`, `campaign_id`,
`task_id`, etc.) and never embed the underlying client, customer, or
personal data itself. Consumers must fetch any actual content (briefs,
assets, signal payloads) from the Vault (Postgres) by id, not from the
queue message body.

This is a deliberate **compensating control** for the dev-environment
decision to run Service Bus Standard SKU without a private endpoint (see
`docs/accepted-risks.md` and `.loop/spec.json` locked_decisions): because
Service Bus is reachable over its public endpoint in dev (auth is
Entra-managed-identity-only, TLS-enforced), the blast radius of that
exposure is limited to task metadata, never personal or client data.

## Redaction rules

Producers MUST apply the following redaction rules before publishing to
either queue:

1. No free-text fields containing names, email addresses, phone numbers,
   physical addresses, or other personal information may be included in
   an envelope's `metadata` bag or any other property.
2. `metadata` values are restricted to short opaque strings (routing
   hints, correlation/tracing ids) — never full request/response bodies.
3. Any field that could carry client-supplied content (brief text, asset
   copy, signal payloads) is represented by an id reference into the
   Vault, never inlined.
4. Producers are expected to run redaction as a pre-publish step (or unit
   test) and reject/strip any envelope that fails this check before
   calling `queue.send`.

Consumers should treat an envelope that unexpectedly contains
personal-data-shaped content as a producer bug and alert rather than
process it.

## Versioning

This is the frozen v1 envelope contract (`$id` contains `/v1/`). A
non-additive change requires a new `/v2/` schema and queue naming
convention; see `scripts/validate_contracts.py` for the CI breaking-change
guard.
