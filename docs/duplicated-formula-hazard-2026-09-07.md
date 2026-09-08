# Duplicated-formula hazard — design note from the context-soundness pilot

Finding (benchmarks/context-soundness-2026-09-07/RESULTS-GROWN.md, SB
scenario): `orders_multi.lines.entitlement` and the inline computation in
`orders_multi.lines.refunded_line` are two copies of the same refund-
rounding rule. The engine's monetary path uses the inline copy; the
`entitled` postcondition (and the v2-v6 obligations registry's prose
descriptions) uses the named function. Consequences observed:

1. **Change-impact blind spot by construction**: patching only
   `entitlement` (the named, contract-visible copy) changes no monetary
   behavior; the impact tool correctly does not propagate to tests
   asserting monetary totals, and a reviewer scanning the diff believes
   the rule changed. The pilot's SB scenario hit exactly this.
2. **Contracts can pin the wrong copy**: an ensures comparing against
   `entitlement(...)` verifies the named copy while the business runs the
   inline one — the clause can hold while the shipped rule differs.

This is not a tooling bug — it is what duplicated business rules do to any
single-copy anchor. GoPyT's contracts make it visible (the clause names
exactly one copy), which is itself useful: the hazard is detectable by
review, and the obligations registry can carry it as an explicit unknown.

Recommendations for the next subject generation (not applied to frozen
subjects):

- Prefer one named function per business rule; have the monetary path CALL
  it (the contracts and the money then share one source of truth).
- When duplication is unavoidable, add a test that pins both copies to the
  same value on a discriminating input (one where the formula class
  differs, e.g. non-divisible entitlements), so divergence traps.
- Registry convention: when a rule has multiple implementations, list every
  copy in the obligation's `functions`, so `impact` propagates through all
  of them.

Status: recorded as a design note; no frozen artifact changed.
