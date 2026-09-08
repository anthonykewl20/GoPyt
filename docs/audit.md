# GoPyT v0 documentation audit and release gate

Current implementation status: [closure audit](implementation-closure-2026-09-05.md); earlier findings remain in the first audit.

Re-audit: 2026-09-04. Status: **spec locked; empirical claims unproven until prototypes pass.**

The original 2026-09-04 audit covered documents before the compiler existed.
A Python-hosted implementation now exists. See the
[2026-09-05 implementation audit](implementation-audit-2026-09-05.md) for defects,
repairs, executed tests and remaining gaps. Neither audit certifies production
readiness.

## Completeness matrix

| Area | Canonical source | Release assertion |
|------|------------------|-------------------|
| purpose | `charter.md` D1–D14 | companions do not silently expand D10 except D14 |
| syntax | `grammar.ebnf` | evolve/egress/observe present |
| static/runtime | `spec.md`, `implementer.md` | closed catalogs |
| stdlib | `stdlib.md` | includes secret, observe, limit, evolve |
| VM | `bytecode.md` | effect bits 0–11 |
| security | `security.md` S30 | egress, Secret, no app ffi |
| evolve/harden | `evolve.md`, `hardening.md` | next-process digest; sketches |
| proof vs hope | `validation.md` | P0–P4 |
| diagnostics | `diagnostics.md` | unique codes; E110 reserved names |
| AI | `ai-surface.md`, `agent-guide.md` | closed world |

## Re-audit resolutions

- Tests (S17/S28): may call real tasks; **no** mocks/interception.
- `Secret` is opaque (no `Secret { }` in app source).
- D14 allows bounded observe + checked evolve; not live `eval`.
- `eval`/`exec`/… → `GOPYT_E110`.
- f64/`i32`/`u32`/`u64`: no arithmetic (S20, bytecode).
- Empirical hardening/evolution/agent-repair are **P0–P4**, not “docs say so.”

## Mechanical gate (unchanged)

1. Markdown links resolve (exact case).
2. No TODO/TBD/FIXME in normative files (historical charter “not yet designed” lines are sequence notes).
3. Keyword, effect, stdlib, opcode, trap, diagnostic catalogs agree.
4. Examples parse or are labelled invalid.
5. Formatter idempotent (needs compiler).
6. Diagnostic codes unique.
7. Opcode/trap numbers unique.
8. Conformance fixtures executable before shipping the compiler.
9. Lockfile digest reproducibility (needs compiler).
10. No `open`/`unresolved` in a runnable package.

P0 (`prototypes/observe/`) must pass before implementing `core.observe` in the VM.
P1–P4 before calling v0 validated.
