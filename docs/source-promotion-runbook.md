# Filling a scan profile's sources — operator runbook

`functions/_shared/scan-profiles.yaml` decides what the signal scanners scan
and where they read it. A profile with no `urls` is refused at dispatch and
its scanner completes as `not_configured` — no model call, no cost, and
nothing on the morning brief. Filling one in is what makes that scanner live.

This is the procedure for doing that. It existed only as comments spread
across `scan-profiles.yaml`, `source-candidates.yaml`,
`source-discovery-loop.yaml`, `dispatch.py` and `check_allowlist_sync.py`,
which is why it is written down here: two approvals, two config edits, and a
week between them is not something to reconstruct from source comments at
the moment a card is sitting in the queue.

## The shape of it, and why it has this shape

A candidate source cannot be evaluated without fetching it, and fetching it
requires allow-listing — which is the decision the evaluation exists to
inform. The pipeline breaks that circularity by splitting the capability in
two, with a human gate in front of each half:

```
function 17 proposes an address   (no retrieval at all — it is permitted nothing)
        ↓  card 1: config.source_promotion
   a person clears the HOST for probing        → edit source-candidates.yaml + MCP_WEB_PROBE_ALLOWLIST
        ↓  (the following week)
probe_url measures its SHAPE      (metadata only: status, feed, item count, text size, 5 titles — never the body)
        ↓  card 2: config.source_promotion
   a person promotes the URL to a scan         → edit scan-profiles.yaml + MCP_WEB_ALLOWLIST
```

Nothing in the loop edits either file or either allow-list. `MCP_WEB_ALLOWLIST`
is a security control (AC-17), not configuration, and a pipeline that could
widen it unattended would be a pipeline that lets discovered content decide
what the system may reach.

**A proposal cannot be probed in the run that made it.** `probe_url` reads
`MCP_WEB_PROBE_ALLOWLIST`, so a host nobody has cleared is unprobeable. That
is deliberate — it is the gate that stops a model's guess from causing a
network call — and it means the minimum time from proposal to live source is
**two weekly cycles**, whatever the queue looks like.

The loop runs Mondays 05:00 SAST
(`infra/modules/scheduling/source-discovery-trigger.bicep`).

## Card 1 — clearing a host for probing

The card's title reads `Probe allow-list — N host(s) proposed for M
unsourced profile(s)`. It carries every proposed address, who publishes it,
which profile it is for, why the scout proposed it, and the scout's own
confidence that the address exists at all.

Read `confidence` as what it says it is: whether the address exists and is
what it claims, never whether the source is any good. A reconstructed feed
path is always `low`, and a list that is mostly `low` is normal rather than
a bad list — the probe is what turns a guess into evidence.

Approving authorises probing only. On approval:

1. Add each approved candidate to `functions/_shared/source-candidates.yaml`
   with a `candidate_id`, its `profile_id`, its `url` and the `rationale`
   from the card.
2. Run `python3 scripts/check_allowlist_sync.py`. It fails, and prints the
   exact `MCP_WEB_PROBE_ALLOWLIST` value `infra/main.bicep` should carry.
3. Paste that value into `infra/main.bicep` and deploy.
4. Re-run the checker. It must exit 0 before you stop.

Skip step 2–4 and the candidate is unprobeable: it scores zero and reads as
unreachable rather than as unconfigured, which is the failure this checker
exists to prevent.

## Card 2 — promoting a probed source into a scan

The card's title reads `Source promotion — N of M candidate(s) recommended
for the scan allow-list`. Each entry carries the measured score, the reasons
that produced it, and up to five sample titles.

The sample titles are the part to actually read. A probe that says "200,
feed, 20 items" cannot tell you whether those items are about your market;
the titles can. A source that scores well and carries nothing relevant is a
source that will spend a scan's evidence budget on noise.

On approval:

1. Add the url to that profile's `urls` in
   `functions/_shared/scan-profiles.yaml`.
2. Run `python3 scripts/check_allowlist_sync.py`, paste the printed
   `MCP_WEB_ALLOWLIST` value into `infra/main.bicep`, deploy, re-run.
3. Leave the candidate in `source-candidates.yaml`. It is the record of what
   was proposed and why, and the promotion card names its `candidate_id` —
   deleting the row breaks the trail from a live url back to the reasoning
   that put it there. The probe skips promoted candidates automatically
   (`dispatch.py`'s `_pending_source_candidates`), so it costs nothing to
   keep.

### Choosing the floors for a newly-sourced profile

`defaults` are `min_sources: 2` / `min_distinct_domains: 2`, and 2/2 is where
a profile should end up. A profile going live with **two or fewer sources**
needs `min_sources: 1` / `min_distinct_domains: 1` as an explicit override,
or a single failing feed fails the whole scan. Say in a comment which it is —
a rollout setting to remove, or a permanent consequence of a short source
list — because the two read identically in the YAML and only one should ever
be reverted.

Watch `ingest_signals_degraded` and `ingest_source_below_content_floor` for
the new `profile_id` afterwards. A url that never returns evidence should be
removed, not tolerated.

## Verifying a profile actually went live

```bash
python3 scripts/check_allowlist_sync.py          # must exit 0
cd services/orchestrator && pytest -q            # the fan-out tests read the YAML directly
```

The scanner's next run writes `status: "scanned"` on its `result_ref`
instead of `status: "not_configured"`.

## When the last profile is filled

`test_some_scanners_are_still_awaiting_sources`
(`services/orchestrator/tests/test_dispatch_fanout_scanners.py`) fails
deliberately at that point. Delete the unsourced-path tests rather than
relaxing it — the behaviour will have become unreachable.

## Known limits

- **LinkedIn company pages cannot be sourced.** Profiles 12 and 13 both name
  them as a channel and `fetch_url` does not authenticate, so such a
  candidate fails its probe. That is a real coverage gap, not a
  configuration oversight to keep retrying. Function 17 is instructed not to
  propose them.
- **A category is not an organisation.** `competitor-register.yaml` marks
  "the Big Four SA data practices" as `kind: category`; it has no newsroom
  and none should be invented for it.
- **Re-probing a live source is not this pipeline's job.** A feed that has
  gone quiet surfaces on the scan path as `ingest_signals_degraded`. A
  promotion card can only recommend a promotion, so it is the wrong place to
  report that a promoted source has died.
