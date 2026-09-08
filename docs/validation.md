# GoPyT v0 claims, proof, and prototypes

Current evidence: [implementation closure audit](implementation-closure-2026-09-05.md), including the interactive P2 trial and bounded P3 integration.

Status: normative for what “done” means. The language docs are not a proof that the language works. This file splits **definitional** rules, **mechanized** checks, and **empirical** claims that need running prototypes.

Nothing here licenses guessing. If a claim is empirical, the prototype must exist before we treat the claim as shown.

## Three kinds of claim

| Kind | Example | Proof |
|------|---------|--------|
| **Definitional** | `int` is illegal; `fn` has no effects | Grammar + checker conformance (`docs/conformance.md`) |
| **Mechanized** | overflow traps; extra JSON key fails | VM/stdlib fixtures, not an LLM |
| **Empirical** | agents write GoPyT via `GOPYT_E*` repairs; CUSUM catches a rate shift; evolve does not ship a failing check | Prototypes below, with numbers |

Not a proof (do not advertise as one):

- “fully secure” / “foolproof”
- “AI will not write the wrong business rule”
- “self-evolving code is always safer”
- RP-001 bounded-context over a huge repo (not a v0 compiler feature)
- Cryptographic authorization (no homemade ciphers; auth is app spec)

## Audit findings (2026-09-04 re-audit) and fixes

| Issue | Resolution |
|-------|------------|
| `Secret { }` looked constructible | Opaque: no user constructor (`stdlib.md`) |
| D10 deferred replay; S31–S32 add observe/evolve | Charter **D14**: bounded observe+evolve is in v0; full replay/capabilities still out |
| `eval` as E074 vs E110 | Reserved dynamic-code names are **E110** |
| Tests vs mocks | **S17/S28**: tests may call real tasks; **no** effect interception. Not pure-only. |
| Hardening math was specified but untested | Prototype `prototypes/observe/` |
| “Docs complete” implied theories proven | This file: they are not, until prototypes pass |
| `audit.md` gate items 4–10 need a compiler | Still true; conformance fixtures land with the compiler |

## Prototype plan (must exist before calling v0 “validated”)

Each prototype has a **pass criterion**. Calibration data for detectors is a **known rate change**, not an app mock (S28).

### P0 — Observe sketches (`prototypes/observe/`)

Implements Welford, Count-Min, Vitter reservoir, Page CUSUM on a Bernoulli failure stream.

Pass:

- Reservoir size never exceeds `K`
- After a planted p: 0.01 → 0.15 shift, CUSUM alarms
- Control stream p=0.01 of the same length: alarm rate empirically low (documented in the prototype’s output)
- Memory of sketches is O(1) in stream length (fixed buffers)

### P1 — Compiler conformance (with the v0 compiler)

Executable `conformance.md` cases C001–C041. Pass: all green.

### P2 — Agent repair loop

Give an agent `ai-surface.md` + `diagnostics.md` + a broken `.gopyt` tree. It may only apply `GOPYT_E*` repairs.

Pass: `gopyt check` exit 0 within a bounded number of repair cycles; no new dialect invented.

### P3 — Auth observe → harden (integration)

A tiny `login` task under `gopyt run`: inject a burst of `Denied`. `report().cusum_alarm` becomes true. `propose` either `NoChange` (if check would fail) or a digest that still `gopyt check`s and includes `core.limit` or a tighter `requires`.

Pass (v0, partial): no `ffi`/egress widening; lock digest changes only if check
passed; observe memory stays bounded. `examples/auth` plus evolve gates cover
the alarm and apply/reject paths. Fitness is check + S30 + non-worsening contract-trap CUSUM on tag-only
reservoir replay (v0 traces do not keep payloads). Mechanical candidates are
allowed; LLM mutation is not required for this pass.

### P4 — Negative security

Attempts: `eval`, app `ffi`, `request` off-allowlist, `log.write(secret)`, JSON extra field.

Pass: E110 / E069 / E111 / E112 / `ConvertError` as specified. No prototype may weaken these.

## Implementation order

1. Lexer/parser/check/fmt against grammar (P1 skeleton)
2. Bytecode + VM for `fn` and traps (mechanized S20)
3. HTTP + `store.db` + `egress` + `Secret` (P4)
4. `core.observe` using the P0 algorithms (port, don’t rewrite)
5. `evolve.propose` last (P3)

Do not implement evolve before P0 and check exist. An unevaluated “living code” story is not v0.
