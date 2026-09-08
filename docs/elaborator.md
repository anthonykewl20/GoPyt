# GoPyT v0 implementation elaborator

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Must follow: charter D2/D11, `docs/spec.md`, `docs/grammar.ebnf`, `docs/interview-tree.md`, `docs/lockfile.md`.

The interview writes **intent** (`spec/`, `gopyt.toml`). This elaborator writes **code** (`impl/`, `test/`). Humans do not drive it. Agents may fill holes; the compiler owns everything that has one legal form.

## Two parts (do not mix)

| Part | Who | Deterministic? | Writes |
|------|-----|----------------|--------|
| **E-D** | compiler | yes, same spec+toolchain → same bytes | impl headers, `use` lists, determined bodies, `unresolved` holes, `gopyt.lock` via check |
| **E-A** | impl agent | no | replaces `unresolved <id>` **only** |

RP-002 applies to E-D (layout, signatures, wiring). Algorithm bodies are E-A; after fill, `gopyt fmt` + `gopyt check` freeze bytes in the lockfile digest. We do **not** pretend two LLM runs emit the same `charge` body.

## Pipeline

```
spec/ + gopyt.toml
    → E-D1  parse/check spec (no impl required for this step internally)
    → E-D2  emit impl/ twins
    → E-A   fill unresolved
    → E-D3  emit test/ from `open needs_test_*` (bodies may still be unresolved)
    → E-A   fill test unresolved
    → gopyt fmt
    → gopyt check     (* verifies spec↔impl and proposes/validates gopyt.lock *)
    → gopyt test / run
```

No extra CLI names. E-D2/E-D3 are invoked as the compiler library used by `gopyt check` when `impl/` is missing: **check does not write impl**. It emits one structured repair per missing file (full canonical text). The elaborator agent writes those files, then fills holes.

## E-D2 — impl file shape

For each `spec/foo/bar.gopyt` emit `impl/foo/bar.gopyt`:

```
module foo.bar

use ... { ... }     (* computed, see below *)

provide Named for User
{
    fn name(value: Self) -> str
    {
        unresolved named_user_name
    }
}

fn normalize(text: str) -> str
{
    unresolved normalize
}

task charge(payment: Payment) -> Receipt | Declined
    effects { network, database.write }
    requires payment.amount > 0
{
    unresolved charge
}
```

Rules:

- **Do not copy** `type` / `enum` / `trait` / `agent` / `http` into impl. Spec of the same module is already in scope.
- **Do copy** every `fn` / `task` / `workflow` header exactly (including contracts and effects).
- **Do copy** every `provide` header. Its impl body contains every trait member
  signature in trait declaration order; each member has its own body.
- Impl-only helpers: allowed as extra `fn`/`type` in `impl/` (private, S15). E-A may add them; E-D2 does not invent `utils`.
- One `unresolved snake` statement is the entire callable/member body until E-A.
  The id is the function name, or `trait_type_fn` in snake
  (`named_user_name`).
- `unresolved` type-checks as “not done”: `gopyt check` fails with `GOPYT_E030 unresolved` and repair “replace this statement with a body”. It is **not** a runtime todo.

## Computed `use` lists (canonical)

E-D2 emits the minimal allowlist for names **actually referenced in this impl file** (headers + body). For newly generated files, E-D computes:

- one `use` per foreign module
- module paths sorted lexicographically
- names in `{ }` : PascalCase first, then snake, each group A–Z
- unused `use` / unused names = error (S4)

After E-A, missing/unused imports are checker repairs. The formatter sorts existing imports; it never adds or removes imports.

## Determined bodies (E-D fills, not `unresolved`)

Only for these structurally determined forms (identity/constructor proofs use the closure amendment):

1. **`serve` on an `http` module**  
   Spec must declare (interview writes this when the first route is added):

   ```
   task serve() -> unit | ListenError
       effects { network, ...union of handler effects... }
   ```

   Body is only:

   ```
   {
       return net.http.serve()
   }
   ```

   No other `serve` implementation is legal. `net.http.serve` is only legal here (`docs/stdlib.md`).

2. **Identity / record-construct wrappers**  
   If the spec `ensures` uniquely determines the return as a constructor of args (checker can prove it), emit that `return Type { ... }`. If not proveable, hole.

3. **`workflow` that is only calls**  
   v0 does **not** auto-sequence tasks (order is not in the spec). Workflows stay `unresolved` unless (2) applies.

4. **`provide Json for Type`**  
   The spec declaration selects a compiler-owned native derivation. No impl body is emitted or permitted. No custom JSON.

5. **`provide FromStr` for a record with a single `str` or `i64` field**  
   Determined compiler-owned native derivation; no impl body is required. Other types need an explicit provide body with unresolved member stubs.

6. **`core.evolve.propose`** when `evolve { }` is present (`docs/evolve.md`).

Everything else is a hole. E-D never guesses Stripe vs a mock.

## E-A — filling holes

The impl agent may:

- replace `unresolved id` with statements that match the header
- add impl-private `fn` / `type` in **this** module file only
- `use` names that fmt will canonicalize

The impl agent may **not**:

- change signatures, effects, contracts, module path, or `spec/`
- add `pub`, methods, `async`, extra files, or `helpers.gopyt`
- call undeclared modules or invent effects
- leave `unresolved` and also “implement” beside it
- implement a `fn` with `CALL_TASK` / effects

If E-A cannot implement without a new public type or effect, it **stops** and the interview must amend `spec/` (new Q round). It does not widen the spec itself.

## E-D3 — tests

Each `open needs_test_<name>` on a public pure callable becomes `test/<module>.gopyt`:

```
module test.math

test add_one_one
{
    unresolved add_one_one
}
```

E-A calls only public surface plus `core.test.assert_eq`. Test effects are
inferred from calls; E-A cannot suppress or intercept them. English is not
written into the file. The completed body ends with explicit `return unit`.

## Canonicalization after fill

`gopyt fmt`:

- 4-space indent, `\n`, keyword spacing from grammar
- `use` lists as above
- preserve literal field evaluation order
- no extra parens
- preserve match arm order

`gopyt check` then:

- spec↔impl header identity
- no `unresolved`
- effects/exhaustiveness/immutability
- refresh-validate `gopyt.lock` (`docs/lockfile.md`)

## Failure mode

If two public ways remain (e.g. extra helper vs inline), pick **inline** (fewer decls). If still two, it is not determined: E-A must have chosen one; fmt does not reshuffle algorithms, only layout.
