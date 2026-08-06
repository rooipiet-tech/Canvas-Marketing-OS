# 18 — The Marketing Code, Analysed

*Every line of executable marketing domain logic in the platform lives in one
file. This document maps it, compares the five handlers structurally, and
identifies what is genuinely marketing-specific versus what is a repeated
pattern waiting to be extracted.*

**Companion extract:** `_marketing-code/dispatch-marketing-extract.py` — a
read-only, banner-annotated copy of the region with original line numbers
preserved, for reading without the surrounding 100 lines of dispatch/gating
machinery.

---

## 1. Where it is

`services/orchestrator/orchestrator/dispatch.py` — 1,138 lines total.

| Lines | Section | Total | Non-comment | Marketing-specific? |
|---|---|---|---|---|
| 62–232 | Constants + client factories | 171 | ~125 | **partly** — function ids, proof-circuit tags |
| 235–317 | Shared helpers (prompt read, JSON parse, complete+meter) | 83 | ~70 | no — generic |
| 320–399 | Ingest redaction fallback | 80 | ~77 | **yes** — but only because of ingest's content |
| 402–440 | `resolve_lineage_result` | 39 | ~34 | no — generic DAG memory |
| **443–558** | **HANDLER 1 · ingest-signals** (fn 09, haiku) | 116 | ~99 | **yes** |
| **561–677** | **HANDLER 2 · draft-brief** (no LLM) | 117 | ~86 | **yes** |
| **680–856** | **HANDLER 3 · qa-review** (fn 02, sonnet) | 177 | ~93 | **yes** |
| **859–949** | **HANDLER 4 · draft-content** (fn 42, sonnet) | 91 | ~72 | **yes** |
| **952–1023** | **HANDLER 5 · request-approval** (no LLM) | 72 | ~49 | **no** — pure governance |
| 1026–1138 | `DISPATCH_TABLE`, pass-through, not-ready/cascade gate | 113 | ~93 | no — generic |

**395 executable lines across the five handlers.** The file is **37%
comments** — an unusually high ratio, and most of it is root-cause narrative
from live incidents, which is the reason this file is hard to read but
valuable to keep.

## 2. The five handlers, compared

Call sequences extracted mechanically, not from reading:

```
ingest-signals          draft-brief            qa-review              draft-content        request-approval
──────────────────      ──────────────────     ──────────────────     ─────────────────    ─────────────────
_load_fetch_sources     resolve_lineage        load_permission_check  —                    resolve_lineage
build_mcp_web_client    —                      resolve_lineage        —                    —
mcp.call_tool ×4        —                      —                      —                    —
build_vault_client      build_vault_client     build_vault_client     build_vault_client   —
get_or_create_campaign  get_or_create_campaign get_or_create_campaign get_or_create_campaign —
—                       vault.get_signal       get_asset / get_brief  —                    —
—                       _render_brief          —                      —                    —
create_agent_run        create_agent_run       create_agent_run       create_agent_run     —
_read_prompt            —                      _read_prompt           _read_prompt         —
emit_task_span          emit_task_span         emit_task_span         emit_task_span       emit_task_span
build_gateway_client    —                      build_gateway_client   build_gateway_client build_gatekeeper_client
_complete_*             —                      _complete_and_meter    _complete_and_meter  gatekeeper.gate_check
_parse_json_content     —                      _parse_json_content    _parse_json_content  —
vault.create_signal     vault.create_brief ×2  —                      vault.create_asset   —
update_agent_run        update_agent_run       update_agent_run       update_agent_run     —
set_result_ref          set_result_ref         set_result_ref ×2      set_result_ref       set_result_ref
db.transition           db.transition          db.transition ×2       db.transition        db.transition
advance_dependents      advance_dependents     advance_dependents     advance_dependents   advance_dependents
```

**Handlers 1, 3 and 4 are the same function with different parameters.**
Handler 2 is that function minus the LLM call. Handler 5 is a different
animal entirely — it makes a governance call and produces no artefact.

### The canonical shape (12 steps)

```python
resolve input            # lineage result_ref | config YAML | module constants
vault.get_or_create_campaign(name, function_id)
vault.create_agent_run(agent_name, campaign_id, function_id, "running", input_payload)
prompt = _read_prompt(function_dir)
user_content = json.dumps({...})
with emit_task_span(task_type, function_id, task_ref, model, run_id) as span:
    response, cost = _complete_and_meter(gateway, vault, model=..., agent_run_id=...)
    set_span_attribute(span, "cost", cost)
    output = _parse_json_content(response["content"])
    artefact = vault.create_<type>(...)
    vault.update_agent_run(id, "succeeded", output, completed_at)
db.set_result_ref(task_id, {...})
db.transition(task_id, COMPLETED, COMPLETED)
db.advance_dependents(task_id)
```

### What actually varies

| Axis | ingest-signals | draft-brief | qa-review | draft-content |
|---|---|---|---|---|
| `function_id` | `09-market-intelligence-director` | `brief.compose` | `02-brand-steward-qa` | `42-linkedin-post-writer` |
| model tier | `claude-haiku` | *none* | `claude-sonnet` | `claude-sonnet` |
| input source | `fetch_sources.yaml` + mcp-web | lineage → `get_signal` | lineage → `get_brief`/`get_asset` | 3 module constants |
| `user_content` shape | topic + horizon + fetched bodies | *n/a* | `{draft_text, client_references, channel}` | `{pillar, proof_point, campaign}` |
| `content_class` | `public_source_content` | *n/a* | `public_source_content` | *none* |
| artefact written | `create_signal` | `create_brief` ×2 | *none* | `create_asset` |
| `result_ref` keys | `vault_signal_id, topic` | `brief_id, executive_brief_id` | `pass, violations` \| `vault_asset_id` | `vault_asset_id, content_hash` |
| terminal branch | — | — | **FAILED / `qa_blocked`** | — |
| agent_name | `market-intelligence-director` | `brief-writer` | `brand-steward-qa` | `linkedin-post-writer` |

**Nine axes of variation. Every one of them is data.** Eight could be
declared in a manifest; only the qa-review terminal branch is behaviour.

## 3. What is genuinely irreducible

Not everything here generalises, and it is worth being precise about which
parts do not:

**(a) qa-review's terminal branch (L830–842).** A `pass: false` verdict
transitions to `FAILED` with reason `QA_BLOCKED` and deliberately **does not
call `advance_dependents`** — so the downstream approval task can never see
the asset. That is a distinct control-flow outcome, not a parameter. Any
generic handler needs an explicit "verdict handler" concept, not just a
schema.

**(b) qa-review's lineage-dependent branch (L760–772).** It inspects
`ancestor_task["task_type"]` to decide whether it is reviewing a brief or a
LinkedIn draft, and sets `channel` accordingly — which then changes *which of
function 02's rules apply* (`internal-brief` exempts CTA and UTM checks).
This is one handler serving two loop positions with different semantics.

**(c) ingest-signals' redaction fallback (L320–399).** 80 lines of
drop-one-source-and-retry logic that exists solely because real news prose
trips the `full-name-like` pattern. It is marketing-specific only by
accident — the underlying problem is "unstructured third-party text meets a
regex firewall", which any ingestion path will hit.

**(d) `_render_brief` (L566–597).** The only deterministic content generation
in the platform. 32 lines, no LLM, byte-identical for identical input. Note
the deliberate choice inside it: sources are cited **by domain only**, never
the bare URL, so that function 02's customer-facing link rules don't fire on
an internal citation.

## 4. Coupling — what the marketing code reaches for

```
dispatch.py handlers
  ├── orchestrator.clients.gateway_client     → model-gateway  (HTTP)
  ├── orchestrator.clients.vault_client_ext   → vault          (HTTP)
  ├── orchestrator.clients.gatekeeper_client  → gatekeeper     (HTTP)
  ├── orchestrator.clients.mcp_client         → mcp-web        (HTTP)
  ├── orchestrator.db                         → task_state     (Postgres)
  ├── orchestrator.telemetry_wiring           → App Insights
  ├── orchestrator.teams_notify               → Teams webhook
  ├── config.functions_dir()                  → prompt.md files on disk
  └── importlib → functions/02/permission_check.py   (dynamic, L218–232)
```

Every dependency is injected through a **module-level factory** —
`build_gateway_client()`, `build_vault_client()`, `build_gatekeeper_client()`,
`build_mcp_web_client()` — deliberately, so a test can substitute exactly one
without faking an httpx transport chain. That is good design and it is what
makes this region testable in isolation.

**The one non-obvious coupling:** `load_permission_check()` (L218–232)
dynamically imports `functions/02-brand-steward-qa/permission_check.py` by
path, because a digit-prefixed directory cannot be dotted-imported (learning
L-0039). It is reused **by reference, never forked** — so the deterministic
uncleared-client check in the orchestrator is literally the same code the
eval harness grades. That is the right call and worth preserving through any
refactor.

## 5. Analysis hooks — where to look first

If you are auditing this region, these are the highest-yield places:

| # | Where | What to look at |
|---|---|---|
| A1 | L707, L786–788 | `client_references` is passed as a **hard-coded empty list**. The deterministic uncleared-client check therefore always passes trivially. The mechanism is real and tested; the data feeding it is not wired. (TD-28) |
| A2 | L760–772 | Both branches set `content_class = "public_source_content"`, so the `full-name-like` redaction pattern is exempted for *all* qa-review calls. Check whether that is still the intent. |
| A3 | L830–843 | The `qa_blocked` path. Confirm `advance_dependents` is genuinely unreachable here — this is the control that stops an unapproved asset reaching the gate. |
| A4 | L923–924 | `output["post"]` — direct key access on model output with no `.get()`. A schema-conformant-but-key-missing response raises `KeyError`, which becomes a generic dispatch failure rather than a clear parse error. |
| A5 | L466–486 | `ingest_signals_handler` tolerates individual `fetch_url` failures but raises only if **all** fail. With mcp-web in fixture mode (TD-31) all four "succeed" with placeholder text. |
| A6 | L274–317 | `_complete_and_meter` swallows cost-lookup failures by design (span cost stays 0.0). Confirm no caller treats `cost == 0.0` as meaningful. |
| A7 | L600–609 | `draft_brief_handler` raises `DispatchError` if no ancestor carries `vault_signal_id`. Since `score-signals` is a pass-through, this depends on `resolve_lineage_result` walking **two** hops. Fragile to loop-shape changes. |
| A8 | L866–876 | `DRAFT_CONTENT_PROOF_POINT`, `DRAFT_CONTENT_PILLAR`, `DRAFT_CONTENT_CAMPAIGN_UTM` — the entire input to function 42 is three module constants. This handler cannot produce a second, different post. |

## 6. The extraction argument

The generalisation case is not speculative — it is visible in the shape table
above. A registry-driven handler would need this signature:

```python
def generic_function_handler(task_id, envelope, db, spec: FunctionSpec) -> None:
    """spec comes from the signed registry manifest, not from Python."""
```

with `FunctionSpec` carrying exactly the nine varying axes:

```python
@dataclass(frozen=True)
class FunctionSpec:
    function_id: str
    agent_name: str
    model: str | None                 # None => deterministic, no gateway call
    input_source: InputSource          # LINEAGE | CONFIG_YAML | CONSTANTS
    input_builder: str                 # name of a registered user_content builder
    content_class: str | None
    artefact: ArtefactSpec | None      # vault method + field mapping
    result_ref_keys: tuple[str, ...]
    verdict: VerdictSpec | None        # the qa-review terminal-branch case
```

That collapses handlers 1, 3 and 4 into one function, keeps handler 2 as the
`model=None` case, and leaves handler 5 (`request-approval`) where it is —
it is governance, not content production, and should not be forced into the
same abstraction.

**The payoff is not line reduction — it is that adding an agent stops being a
code change.** Today, wiring one of the 20 inert packages requires editing
`DISPATCH_TABLE`, editing the Dockerfile's staging list, and editing a loop
YAML. Under a registry-driven handler it is a manifest entry.

**Before doing this**, add the guard test that makes the current gap visible:
assert every `task_type` in any `loops/*.yaml` either has a `DISPATCH_TABLE`
entry or sits on an explicit intentional-pass-through allowlist. That test
converts TD-01 from invisible to loud, and it is the regression net the
refactor needs.

## 7. Reading order

For a first pass, read in this order rather than top to bottom:

1. **L1091–1138** `dispatch_task` — the entry point and the not-ready/cascade gate. Understand what must be true before a handler runs.
2. **L402–440** `resolve_lineage_result` — how a handler finds its input. Everything else depends on this.
3. **L878–949** `draft_content_handler` — the shortest complete example of the canonical shape.
4. **L443–558** `ingest_signals_handler` — the same shape with external I/O and the redaction fallback.
5. **L694–856** `qa_review_handler` — the same shape plus a terminal branch and a lineage-dependent rule set. The most complex of the five.
6. **L561–677** `draft_brief_handler` — the deterministic case.
7. **L952–1023** `request_approval_handler` — the governance case.

Skip the comment blocks on the first pass; return to them when something
looks arbitrary, because they almost always explain a live incident.
