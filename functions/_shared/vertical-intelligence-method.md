# Shared method - vertical intelligence packages

This fragment is the single source of truth for the method shared by all six
`functions/18-0N-vertical-intel-*` packages. Each vertical's `prompt.md`
pastes the block below byte-for-byte between its own matching pair of
begin/end markers (see the literal markers further down this file), then
appends vertical-specific watchlist detail *after* the block, never
interleaved. A future edit to the shared rule happens here once, instead of
drifting six ways.

SHARED:BEGIN

## Earn-your-slot rule

A vertical signal earns a card only if it is retrievable, dated and
attributable to a source a reader could open themselves. Thin evidence never
earns a drop of the qualifier: a single vendor-self-reported claim, an
undated forum post, or a rumour with no retrievable source is described with
`evidence_grade: "light"` rather than dropped from `evidence_grade`
altogether or rounded up to look better supported than it is. Light evidence
is still worth a card - Canvas would rather show a thin signal honestly
labelled than hide it or overstate it.

## Listening scopes

Every vertical package listens across the same three scopes, then applies
its own vertical lens on top:

1. **CFO-office pain language in multi-entity groups.** Grounded in
   `docs/positioning.md` section 4's voice-of-customer language from the CFO
   pre-meeting survey: "More than 3 days a month" lost to reporting,
   reconciling systems and Excel manipulation; "No more Excel accounting";
   a "different number for the same question" across finance, operations and
   commercial; groups "waiting on trial balances, intercompany matrices,
   consols". A card in this scope reports where that pain surfaces publicly
   - a job posting, a conference talk, a tender document, a CFO interview.
2. **The Microsoft Fabric conversation.** Adoption, migration, partner and
   tender movement around Microsoft Fabric, Power BI and the wider Microsoft
   data stack, per `docs/positioning.md` section 3 pillar 3 (Fabric-native).
3. **The Sage ecosystem.** Movement in the Sage partner and product
   ecosystem - Sage X3, Sage 300, Sage Intacct, Sage-native analytics and
   the productised-platform story `docs/positioning.md` section 3 pillar 4
   describes.

SHARED:END
