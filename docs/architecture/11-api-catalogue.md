# 11 — API Catalogue

*Every HTTP surface in the platform. Auth column reflects what the code
actually enforces, not what it should enforce.*

---

## 1. Surface summary

| Service | App | Ingress | Auth enforced | Routes |
|---|---|---|---|---|
| Orchestrator | `ca-orchestrator` | internal | **none** | 3 |
| Model Gateway | `ca-model-gateway` | internal | **none** | 2 |
| Gatekeeper (internal) | `ca-gatekeeper` | internal | **none** | 4 |
| Gatekeeper (approval) | `ca-gatekeeper-approval` | **external** | Entra Easy Auth (Return401) | 2 |
| Publisher | `ca-publisher` | internal | **none** | 3 |
| Vault | `ca-vault` | internal | **none** | 40 |
| Console | `ca-console` | **external** | Entra Easy Auth + code backstop | 9 |
| mcp-web / mcp-buffer / mcp-canva | internal | **none** | 2 each |

**Total: ~69 routes. Two are reachable from the internet. Both are Entra-protected.**

---

## 2. Orchestrator — `services/orchestrator/main.py`

| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/health` | Static liveness. **No DB access** — must return 2xx before the DB is reachable (AC-017) | `{"status":"ok"}` |
| GET | `/status` | Every in-flight task with full state history. Synchronous DB read, **no cache**. Degrades to `[]` if the DB is unreachable | `[{task_id, task_type, state, retry_count, transitions[...]}]` |
| GET | `/runs/{task_ref}` | Agent-native run state: every stage via `depends_on` lineage, span presence, and the **real** Gatekeeper approval status | `{task_ref, stage_count, stages[], span_presence, approval_decision_status}` |

`GET /runs/{task_ref}` returns **503** (not 500) if the lookup fails, and
**404** for an unknown `task_ref`. `span_presence` is one of
`present | absent | not_checked` — never an error.

---

## 3. Model Gateway — frozen contract `contracts/model-gateway/openapi.yaml`

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/completions` | Provider-agnostic completion with routing, redaction, budget, caching, metering |
| GET | `/v1/health` | Liveness |

### Request (`CompletionRequest`)
```jsonc
{
  "model": "claude-sonnet",              // required — LOGICAL id from routing.yaml, never a provider model id
  "messages": [                           // required, minItems 1
    {"role": "system|user|assistant|tool", "content": "string"}
  ],
  "agent_run_id": "uuid",                // required — FK to Vault agent_runs.id, drives cost attribution AND budget
  "max_tokens": 1024,                     // default 1024
  "temperature": 0.7,                     // default 0.7
  "tools": [ {...} ],                     // free-form passthrough — IS redaction-scanned
  // --- additive fields, not in the frozen v1 schema (which sets no additionalProperties:false) ---
  "task_ref": "string",                  // idempotency key: one compute() per task_ref
  "deliberate": false,                   // reasoning hint, feature-flagged → 400 NOT_IMPLEMENTED while disabled
  "content_class": "public_source_content" // maps to a reviewed pattern-exemption allowlist
}
```

### Responses
| Code | Body | When |
|---|---|---|
| 200 | `CompletionResponse` + additive `routing_tier`, `budget_state`, `cache_hit`, `cost_id` | success |
| 400 | `INVALID_REQUEST` | schema violation — **message never contains a submitted value** |
| 400 | `UNKNOWN_MODEL` | model not in `routing.yaml` — message built from config-side facts only |
| 400 | `NOT_IMPLEMENTED` | `deliberate: true` while the flag is off |
| 400 | `REDACTION_BLOCKED` + `routing_tier`, `redaction_outcome` | firewall hit; `gate_decisions` row written; provider never called |
| 429 | `BUDGET_EXHAUSTED` + `queued_task_ref` | hard breach; the value is the escalated `gate_decisions.id` |
| 500 | `PROVIDER_ERROR` | upstream non-2xx |
| 500 | `INTERNAL_ERROR` | anything else — **fixed literal message, never `str(exc)`** |

**Three data-leak defences worth naming**, each with its own finding code:
`DR-3` — schema violations report the JSON path and the failed keyword, never
the instance. `DR-4` — matched-pattern ids are contract-side coordinates
(`fixture:client_names:0`), never the matched text. `DR-5` — the unknown-model
message is built from `routing.yaml`'s own contents, never the submitted
`model` string, because routing runs before the firewall and writes no audit
row.

---

## 4. Gatekeeper — internal (`ca-gatekeeper`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/gate-check` | Evaluate autonomy policy, write exactly one `gate_decisions` row, optionally raise an approval, optionally issue a gate token |
| GET | `/decisions/{decision_id}` | Agent-native read-back of a decision (AC-17) |
| GET | `/approval-status` | The **real** pending/approved/rejected/expired state, from `approval_inbox` |
| GET | `/healthz` | Liveness |

### `POST /gate-check`
```jsonc
// request
{ "agent_run_id": "uuid",        // required, must parse as uuid → else 400
  "function_id": "publish.social_post",
  "action_class": "publish",
  "content_hash": "sha256 hex",
  "preview_title": "[LOOP-PROOF] publish.social_post (publish)",
  "preview_reference": "loop-proof://<task_id>",
  "evidence_summary": "...",
  "subject": "optional JWT sub, defaults to agent_run_id" }

// response
{ "decision_id": "uuid", "agent_run_id": "uuid",
  "outcome": "approved|rejected|escalated",
  "reason": "level_0_blocked|level_1_requires_approval|level_2_requires_approval|
             level_1_approved_by_human|level_2_approved_by_human|
             level_3_auto_approved|level_4_autonomous_passthrough|
             kill_switch_active:<scope>[:fn]",
  "level": 0-4, "function_id": "...", "action_class": "...",
  "gate_token": "<JWT, only when outcome=approved>",
  "approval_route": "teams|inbox",
  "approval_id": "uuid", "approve_url": "...", "reject_url": "...",
  "approval_expires_at": "iso8601" }
```

**Decision order is a security control:** (1) kill switch — uncached live
SELECT, before anything else; (2) level 0; (3) levels 1–2 → look for a prior
approval matching `(agent_run_id, function_id, content_hash)`, else escalate
+ create link + dispatch card; (4) levels 3–4 → auto-approve. A gate token is
issued **only** on an `approved` outcome.

### `GET /approval-status?agent_run_id=&function_id=&content_hash=`
Exists because a `gate_decisions` row's `outcome` is `escalated` the instant
the approval is raised and **never itself becomes approved** — a new row is
only inserted on the *next* `/gate-check`. This endpoint reads
`approval_inbox`, which *is* mutated in place by the click. Returns
`{"status": "not_found"}` rather than 404 when no inbox row exists.

---

## 5. Gatekeeper — approval (`ca-gatekeeper-approval`, EXTERNAL)

| Method | Path | Auth |
|---|---|---|
| GET | `/approval-action/{link_token}?choice=approve\|reject` | **Entra Easy Auth, Return401** |
| GET | `/healthz` | none |

| Status | Outcome | Audit row |
|---|---|---|
| 200 | `approved` / `rejected` | `approval_actions` + new `gate_decisions` row |
| 401 | no authenticated principal | none (rejected before token lookup) |
| 404 | unknown token | none — *"disclosing more would turn this into a token oracle"* |
| 409 | `link_already_used` | `approval_actions` |
| 410 | `link_expired` | `approval_actions` + `approval_inbox.status='expired'` |

---

## 6. Publisher — `ca-publisher`

| Method | Path | Purpose |
|---|---|---|
| POST | `/publish` | Verify a gate token and publish, or refuse with a recorded reason |
| GET | `/publish-attempts/{id}` | Agent-native read-back of any attempt, including every refusal |
| GET | `/healthz` | Liveness |

```jsonc
// request
{ "agent_run_id": "uuid",
  "function_id": "publish.social_post",
  "asset_bytes_b64": "<base64 of the EXACT bytes>",  // hash recomputed from these
  "gate_token": "<JWT>",
  "asset_id": "uuid" }                                // optional; if present, lookup failure FAILS CLOSED
```

**403 on every refusal**, with the full `PublishResponse` in `detail`. See
`02-module-catalogue.md` §M4 for the complete 12-branch refusal matrix and
the security rationale for its ordering.

---

## 7. Vault — `contracts/vault-api.yaml` (OpenAPI 3.1, 1,623 lines)

### The generic object surface — 9 types × 4 verbs

| Path segment | Object type | PATCH | DELETE |
|---|---|---|---|
| `/signals` | signals | ✅ | ✅ |
| `/campaigns` | campaigns | ✅ | ✅ |
| `/opportunity-cards` | opportunity_cards | ✅ | ✅ |
| `/briefs` | briefs | ✅ | ✅ |
| `/agent-runs` | agent_runs | ✅ | ✅ |
| `/assets` | assets | ✅ | ✅ |
| `/gate-decisions` | gate_decisions | ❌ **append-only** | ❌ |
| `/costs` | costs | ❌ **append-only** | ❌ |
| `/consent-register` | consent_register | ✅ *revoke only* | ❌ |

`POST /{type}` · `GET /{type}?limit&offset` · `GET /{type}/{id}` ·
`PATCH /{type}/{id}` · `DELETE /{type}/{id}`

**Every create requires all six taxonomy fields:**
```jsonc
{ "vertical": "mobility", "function_id": "signal-ingestion-v1",
  "campaign": "uuid",                          // FK to campaigns
  "evidence_grade": "A|B|C|D|unverified",
  "consent_status": "granted|revoked|not_required|pending",
  "retention_class": "ephemeral_30d|standard_1y|extended_3y|legal_hold",
  // + client-derived fields, which trigger the consent gate:
  "data_subject_ref": "...", "consent_channel": "...", "consent_purpose": "..." }
```

| Code | Meaning |
|---|---|
| 201 | created |
| 403 | `consent_required` — client-derived write with no active consent (+ audit row) |
| 422 | `taxonomy_field_missing` / `taxonomy_field_invalid` / `taxonomy_field_immutable` / `fk_violation` / `field_missing` (+ audit row) |
| 404 | `not_found` |

Error bodies are `{"error": {"message", "code", "field?"}}`. Note that
`main.py` installs a custom `HTTPException` handler because FastAPI's default
re-wraps `exc.detail` under a further `"detail"` key — which meant *every*
taxonomy/consent/not-found rejection this service ever returned had no
top-level `error` key at all, contradicting the contract.

`/assets` is special-cased: `content_base64` on create (→ content-addressed
blob, `content_hash` + `storage_uri` filled server-side) and on read.

### Governance and reporting routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/consent/check?data_subject_ref=&channel=&purpose=` | `{consented, consent_register_id}` |
| POST | `/retention-expiry-runs` | Runs the sweep inline, returns the run row (202) |
| GET | `/retention-expiry-runs` · `/{id}` | Run history |
| POST | `/utilisation/rollup` | Recompute a day's rollup |
| GET | `/utilisation/rollup?from=&to=&object_class=` | `[{date, object_class, caller_service, read_count}]` |
| GET | `/health` | Static — **no DB round-trip** |

`X-Caller-Service` on any object GET feeds `access_log` → `utilisation_daily`.
It is **informational only and explicitly not a trust boundary**.

---

## 8. Console — `ca-console` (EXTERNAL)

All routes require an Easy-Auth principal (401 otherwise), enforced both by
the ingress and by a `require_principal` `Depends`. Every GET honours
`Accept: application/json` and returns the same data the HTML renders.

| Method | Path | Query params |
|---|---|---|
| GET | `/` | → 307 `/tasks` |
| GET | `/tasks` | — |
| GET | `/tasks/{task_ref}/trace` | `task_ref` must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` else **400** |
| GET | `/approvals` | — |
| GET | `/vault-search` | `object_type`, `vertical`, `function_id`, `campaign`, `evidence_grade`, `consent_status`, `retention_class` |
| GET | `/costs` | `group_by=function\|day`, `date` |
| GET | `/kill-switch` | — |
| POST | `/kill-switch/toggle` | JSON or form-urlencoded; form path adds an Origin/Referer same-origin check and returns 303 |
| GET | `/health` | unauthenticated |

---

## 9. MCP servers — one protocol, three servers

`POST /mcp` (JSON-RPC 2.0) + `GET /health`.

| Method | Params | Returns |
|---|---|---|
| `initialize` | — | protocol version + server info |
| `tools/list` | — | tool definitions **including the authoritative `inputSchema`** (which lives in `app/main.py`'s `TOOLS` list, never in `tools.yaml`) |
| `tools/call` | `{name, arguments}` | `{content[], structuredContent{}}` |

| Server | Tools | Behaviour |
|---|---|---|
| mcp-web | `fetch_url(url)` | Allowlist checked **before** any network call; sliding-window rate limit; fixture unless `MCP_WEB_LIVE_MODE` |
| mcp-buffer | `list_queue(channel_id)`, `get_post(post_id)`, `create_draft(channel_id, text)` | `create_draft` takes **exactly two arguments**; status hardcoded server-side |
| mcp-canva | `create_design_from_template(template_id, ...)`, `bulk_create_from_csv(template_id, ...)`, `export_design(design_id, format)` | `template_id` required on both creation tools |

Every call is best-effort logged to `mcp_ops.tool_calls`; a Postgres outage
never fails a tool call.

---

## 10. Cross-cutting API conventions

| Convention | Applied where |
|---|---|
| `/health` or `/healthz` is dependency-free | every service — must return 2xx before the DB is reachable |
| W3C `traceparent` injected outbound, joined inbound | all orchestrator clients + every service's `telemetry_wiring.py` |
| `X-Correlation-Id` returned | Vault (one JSON log line per request) |
| Error bodies never echo submitted values | model-gateway (DR-3/4/5), routing, redaction |
| Errors carry a machine-readable `code` | model-gateway, Vault, Publisher, Gatekeeper |
| Never hardcode a service FQDN | every client — `az containerapp show` or an env override (L-0025) |
| Agent-native read-back for every write | `/decisions/{id}`, `/publish-attempts/{id}`, `/approval-status`, `/runs/{task_ref}` |

---

## 11. API gaps

| Gap | Consequence |
|---|---|
| **No authentication on 5 of 8 services** | Network isolation is the only control |
| **No REST over `kill_switches` / `approval_inbox`** | The console cannot show production governance state (TD-10) |
| **No server-side filtering on Vault list endpoints** | The console fetches everything and filters in Python |
| **No pagination metadata** | `limit`/`offset` with no total count |
| **No bulk endpoints** | 1,000 objects = 1,000 round trips |
| **No webhooks / subscriptions** | Consumers must poll |
| **No API versioning outside model-gateway** | Only `/v1/completions` is versioned in its path |
| **No rate limiting except mcp-web** | Any internal caller can saturate any service |
| **OpenAPI specs exist but are never rendered** | No developer portal, no generated client |
