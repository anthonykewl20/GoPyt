# GoPyT v0 agent protocol

Status: normative for AI-driven implementation and maintenance.

## Read order

Read `ai-surface.md`, `security.md`, `evolve.md`, and `hardening.md` first, then
`charter.md`, `spec.md`, `grammar.ebnf`,
`implementer.md`, `stdlib.md`, `bytecode.md`, `diagnostics.md`, `fmt.md`,
`elaborator.md`, `lockfile.md`, then `conformance.md`. Do not use the historical
research notebook as an API reference.

## Closed-world rule

Treat every vocabulary as closed. A name, syntax form, effect, stdlib call,
diagnostic, opcode, manifest key, or runtime behavior not explicitly present in
the normative documents does not exist. Never infer Go, Python, Rust, TypeScript,
or JavaScript behavior from visual similarity.

If required behavior is absent or two normative statements cannot be reconciled
using S27 precedence:

1. stop the affected implementation path;
2. report the exact files and clauses in conflict;
3. emit `GOPYT_E001 internal` in a compiler path;
4. propose a documentation amendment and conformance case;
5. do not implement either guessed interpretation.

## Change protocol

For each coding task:

1. Identify the public declarations and allowed effects from `spec/`.
2. Inspect the exact stdlib signatures; never invent a helper or alias.
3. Change only the matching `impl/` module. A new public type, callable, effect,
   route, agent task, dependency, or contract requires a spec change first.
4. Use exhaustive `match`, explicit conversions, explicit returns, bounded
   parallelism, and bind-once names.
5. Run `gopyt fmt`, `gopyt check`, relevant tests, then conformance. Static
   failures are fixed before behavioral tests; test effects remain explicit.
6. Apply only the diagnostic's single repair. If that repair would change intent,
   return to the interview instead of widening the contract.
7. Report evidence: changed contract ids, diagnostics resolved, tests executed,
   and remaining `open`/`unresolved` ids. Never claim success from source review.

## Prohibited agent behaviors

- Do not add compatibility aliases, convenience overloads, implicit conversions,
  wildcard matches, default bounds, hidden retries, fallback providers, or
  permissive parsers.
- Do not turn prose into executable intent or silently normalize an illegal name.
- Do not modify `spec/` while filling an implementation hole.
- Do not suppress a checker error, catch a VM trap as a business result, or use
  FFI to evade the type/effect system.
- Do not claim deterministic algorithm generation. Only compiler elaboration,
  formatting, ordering, and artifacts are deterministic; agent-written bodies are
  frozen and audited by digest and `gopyt check`.
- Do not add effect interception or a hidden mock runtime. Task tests call the
  explicit implementation and retain all inferred effects (S28).

## Required handoff record

An implementation handoff contains: toolchain id, package digest, spec files
changed, implementation files changed, exact commands and exit codes, failed or
skipped tests, and every remaining `open` or `unresolved` id. Empty categories are
written as `none`; they are never omitted.
