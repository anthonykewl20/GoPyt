# GoPyT v0 AI surface

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Status: normative. This is the product rule: **the language is small and closed so an agent makes fewer implementation mistakes.**

Humans do not read impl. Agents emit code. Anything an agent can spell two ways, or guess from Python/Go/TS, becomes production errors. v0 removes those degrees of freedom.

## Three buckets

| Bucket | Who decides | Agent job |
|--------|-------------|-----------|
| **Illegal** | compiler (`gopyt check`) | do not emit; if you did, apply the **one** `GOPYT_E*` repair |
| **Determined** | compiler / formatter / elaborator-D | do not invent; copy the repair or the elaborated form |
| **Open logic** | agent, only inside `unresolved` | fill **one** body that matches the spec header |

If a change needs a new type, effect, route, or module, **stop** and amend `spec/` via the interview. Do not “just add a helper.”

## Illegal (cannot compile — fewer hallucinations)

An agent cannot successfully emit:

- a second spelling (`int`/`string`/`dict`/`UserID`/`getUser`/`async`/`class`/`null`)
- a second control style (`if` as a value, `_` in `match`, `while`, ternary, `&&`)
- hidden world I/O (`fn` that logs, fetches, or writes)
- a call without `use`, or a name not on the allowlist
- a non-exhaustive `match` (new error variant is a compile error everywhere)
- implicit conversion, unwrap `!`, default arguments, overloads, methods (`x.fn()`)
- hidden effect interception or dynamically substituted providers (S28)
- extra stdlib (`requests`, `utils`, `console.log`)
- per-request model calls for “learning”; use `observe` + CUSUM (`docs/hardening.md`)
- dynamic eval/exec operations, app FFI, unlisted outbound origins, and opaque
  `Secret` values in logs/JSON (S30)
- `open` holes (except a discharged `needs_test_` on a public callable)
- `unresolved` left in a shipped `impl/`

Wrong program → **check fails before `run`**. That is how we avoid “code that just causes errors.”

## Determined (agent must not restyle)

The compiler/fmt owns:

- file tree (`spec/` `impl/` `test/`), module path = file path
- `use` lists (minimal, sorted)
- canonical source bytes (`gopyt fmt`)
- `provide Json` bodies, `serve` body, 1-field `FromStr` when the spec asked
- bytecode, lock digest, diagnostic text

Two agents formatting the same tree must match. Two agents filling `charge` need **not** match; the digest records what shipped (elaborator.md).

## Open logic (the only place to “write code”)

Inside a hole, the agent may use only:

- spec types and signatures already in scope
- stdlib names in `stdlib.md`
- `match` / `if` statement / bounded `for` / `parallel max N timeout_ms M`
- bind-once names, explicit `return`

It may not add public API, new effects, new files, or a second way to do the same thing.

Business-logic mistakes that still typecheck are **not** solved by more syntax. They are solved by tighter `requires`/`ensures` on the spec (interview), then check.

## Agent loop (no extra tools)

```
spec/  →  gopyt fmt  →  gopyt check  →  fill GOPYT_E* repairs
       →  gopyt test   (effects inferred and preserved)
       →  gopyt run    (selected entry task)
```

Never: hide an effect, skip check, or “try it in Python first.”

## What this does not claim

- The model will never pick the wrong formula inside a legal body.
- Logs are auto-inserted (they are not; `log` is an explicit effect).
- Two LLM runs emit the same algorithm.

It **does** claim: most slop never becomes a runnable program.
