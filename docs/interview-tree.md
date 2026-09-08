# GoPyT v0 interview tree

Must follow: `docs/charter.md` D11, `docs/spec.md`, `docs/grammar.ebnf`.

This is the **only** elicitation state machine. The interviewing agent asks **one node at a time**. Humans pick or type. The agent writes `spec/` (and `gopyt.toml`). It never compiles a prompt. It never skips to `impl/` until the spec checks.

Anti-slop:

- Closed choices where the spec has a closed set (effects, http verbs, yes/no).
- Names validated against S5 / grammar **before** the next question.
- Illegal names: do not “fix silently”. Re-ask with the exact rule.
- Uncheckable rules become `open <id>`, never comments.
- No YAML, no Markdown spec (S1).

## Output

```
<pkg>/
  gopyt.toml
  spec/...
```

`impl/` and `test/` are created by the implementation elaborator, not by this interview.

Write files **after each node that has Writes**, so a dropped session still has a legal prefix.

`gopyt.toml` v0 (see `docs/lockfile.md`):

```
name = "<snake>"
version = "0.1.0"
```

`[deps]` only if Q001d added a path dep. No ranges, no git.

## How to run a node

1. Show `Ask`. If `Choices`, show them as A/B/C… (plus free-text only when `Input` says so).
2. Validate with `Accept`. On failure: print the rule, stay on this node.
3. Apply `Writes`.
4. Follow `Next`. `loop` means stay until the human chooses the stop option.

Do not invent nodes. If something is missing, add an `open` hole and continue.

---

## Nodes

### Q001 — package name

- **Ask:** What is the package name?
- **Input:** one `snake` (grammar). Examples: `billing`, `orders`.
- **Accept:** `snake`, not a keyword, not one letter.
- **Writes:** `gopyt.toml` `name`. Create dirs `spec/`, `impl/`, `test/`.
- **Next:** Q001d

### Q001d — path dependencies

- **Ask:** Add a path dependency?
- **Choices:** A. No  B. Add `{ name, relative path }`
- **Accept:** dep name `snake`; path sibling `../name` or subdir; target has `gopyt.toml` with that `name`.
- **Writes:** `[deps]` entry. Re-ask until A.
- **Next:** Q002

### Q002 — product shape

- **Ask:** What does this package expose?
- **Choices:**
  - A. HTTP API only
  - B. Agent only
  - C. HTTP API and agent (default for a full app)
- **Writes:** session flags `want_http`, `want_agent`.
- **Next:** Q003

### Q003 — more modules later?

This node is a marker. **Next:** Q010 (entities). Modules are derived from entities + api/agent, not from a free-form folder brainstorm.

### Q010 — add an entity?

- **Ask:** Add a domain type (entity), or stop types?
- **Choices:** A. Add type  B. Done with types
- **Next:** A → Q011  B → Q040

### Q011 — type name

- **Ask:** Type name?
- **Input:** `pascal` (`Payment`, `UserId`, `HttpClient` — not `HTTPClient`).
- **Accept:** S5 PascalCase; not a builtin; not already used.
- **Writes:** remember `current_type`.
- **Next:** Q012

### Q012 — type kind

- **Ask:** Is this a record of fields, an enum of variants, or a field-less marker (e.g. `NotFound`)?
- **Choices:** A. Record  B. Enum  C. Marker (`type Name { }`)
- **Writes:** kind. If C: write `spec/<module>.gopyt` type stub (module Q013).
- **Next:** C → Q013 then Q010; A → Q014; B → Q016

### Q013 — which module owns this type?

- **Ask:** Module path for this type? (file = `spec/<path with / not dots>.gopyt`)
- **Input:** `module_path` (`billing`, `billing.api` later for handlers — **types usually `billing` not `billing.api`**).
- **Accept:** dotted snake; suggest existing modules as choices plus “new path”.
- **Writes:** ensure `spec/<path>.gopyt` exists with `module <path>`. Append the type.
- **Next:** back to the kind’s field/variant loop, or Q010 if marker.

Recommended default if the human shrugs: module = package name (`Q001`).

### Q014 — add field?

- **Ask:** Add a field to `<Type>`, or done?
- **Choices:** A. Add field  B. Done
- **Next:** A → Q015  B → Q010

### Q015 — field

- **Ask:** Field name and type? (`amount: i64`, `email: str?`, `status: Status`)
- **Input:** `snake : TypeExpr` using only builtins, existing types, `T?`, `list[T]`, `map[K, V]`.
- **Accept:** parse TypeExpr; unknown type → stay, offer Q011 to create it, or reject.
- **Writes:** append field. No commas.
- **Next:** Q014

### Q016 — add variant?

- **Ask:** Add enum variant to `<Type>`, or done?
- **Choices:** A. Add variant  B. Done
- **Next:** A → Q017  B → Q010

### Q017 — variant

- **Ask:** Variant name? Payload fields or none?
- **Input:** `pascal` plus optional field list like Q015.
- **Writes:** append variant.
- **Next:** Q016

### Q040 — add a trait?

- **Ask:** Add a `trait` (shared `fn` signatures), or skip?
- **Choices:** A. Add trait  B. Done with traits
- **Next:** A → Q041  B → Q045

### Q041 — trait

- **Ask:** Trait name (`pascal`) and members. Each member is a `fn` **or** a `task` with an effect list (`docs/spec.md` S26). First arg is often `Self`. Not `workflow`.
- **Writes:** `trait` in the module from Q013-style prompt (default: package module).
- **Next:** Q040

### Q045 — provide?

- **Ask:** Declare `provide Trait for Type` (this package owns Type)?
- **Choices:** A. Add provide  B. Done
- **Accept:** Type in this module; Trait exists; no duplicate pair.
- **Writes:** `provide Named for User` in spec (no body).
- **Next:** A → Q045  B → Q046

### Q046 — JSON?

- **Ask:** Should any record/enum be JSON (`provide Json for Type`)? Needed if it crosses HTTP or `data.json`.
- **Choices:** A. Add provide Json  B. Done
- **Accept:** Type in this package; `core.convert.Json`.
- **Writes:** `provide Json for Type` in the type’s module (body later, E-D, one encoding).
- **Next:** A → Q046  B → Q020

### Q020 — failures

- **Ask:** Which failure types can public tasks return? (create markers if missing)
- **Input:** list of existing marker/record types, or A. add `NotFound`-style marker via Q011  B. done
- **Writes:** session `failure_types`.
- **Next:** Q030

### Q030 — add a task?

- **Ask:** Add a public `task` (API handler, agent tool, or workflow step), or stop tasks?
- **Choices:** A. Add task  B. Done with tasks
- **Next:** A → Q031  B → Q050

### Q031 — task name and module

- **Ask:** Task name (`snake`) and module path?
- **Accept:** not colliding with a type by case-fold (S5); module exists or create.
- **Writes:** `current_task`.
- **Next:** Q032

### Q032 — kind

- **Ask:** Is this a `task`, a pure `fn`, or a `workflow` (orchestration only)?
- **Choices:** A. task  B. fn (no effects)  C. workflow
- **Next:** Q033

### Q033 — parameters

- **Ask:** Parameters as `name: Type` list. Empty allowed (`()` ).
- **Accept:** each type already exists or builtin.
- **Next:** Q034

### Q034 — return type

- **Ask:** Return type? Prefer `Success | Failure | …` unions for tasks.
- **Accept:** TypeExpr. If it uses `f64` in a later requires, that will be illegal (S20) — warn now.
- **Next:** if kind=fn → Q037; else Q035

### Q035 — effects

- **Ask:** Which effects? (closed list; pick all that apply)
- **Choices (multi):** `network` `filesystem.read` `filesystem.write` `database.read` `database.write` `time` `random` `log` `model` `secret` `observe`  plus  Z. none. Never `ffi` in app code. If `network` or `model`, Q052 writes `egress { }`.
- **Accept:** subset of S9. Empty effects on `task` → re-ask: make it `fn` or pick effects.
- **Writes:** `effects { ... }`
- **Next:** Q036

### Q036 — requires

- **Ask:** Precondition as a bool expression on params (`p.amount > 0`), or `open <id>`, or none (`true` omitted)?
- **Input:** Expr **or** `open snake` **or** skip.
- **Accept:** S11 / S20 (no `f64` in contracts). On English prose: reject; offer `open <id>`.
- **Next:** Q037

### Q037 — ensures

- **Ask:** Postcondition on `result`, or `open <id>`, or skip.
- **Accept:** same as Q036. `match result { ... }` must be exhaustive on the return union.
- **Writes:** append `fn`/`task`/`workflow` **signature only** to `spec/<module>.gopyt`.
- **Next:** Q030

### Q050 — HTTP?

- **Ask:** (skip if Q002 was B) Add HTTP routes?
- **Choices:** A. Add route  B. Done with HTTP
- **Next:** A → Q051  B → Q060

### Q051 — route

- **Ask:** Verb, path, handler task? Example: `post /charge post_charge`
- **Choices for verb:** get post put patch delete
- **Accept:** handler task exists in **this** module; path string; `{name}` matches a `str` (or `from_str`) param.
- **Writes:** `http { }` block in that module (create `*.api` module if they asked for HTTP and have no module yet: `<pkg>.api`). First route also writes:

```
use core.status { ListenError }

task serve() -> unit | ListenError
    effects { network, <union of handler effects> }
```

Do not let the human name this task anything else. Return type is frozen.
- **Next:** Q052

### Q052 — egress

- **Ask:** If this package calls outbound HTTP or `core.model.complete`, list `https://` prefixes (or localhost http). Required for `net.http.request`.
- **Input:** zero or more URL prefixes.
- **Writes:** `egress { "https://api.example.com" }` in that module. Empty only if there is no outbound `network`/`model`.
- **Next:** Q050

### Q060 — agent?

- **Ask:** (skip if Q002 was A) Define an agent bundle?
- **Choices:** A. Add agent  B. Done with agents
- **Next:** A → Q061  B → Q070

### Q061 — agent

- **Ask:** Agent name (`pascal` like `BillingAgent`), module, max effects, task list.
- **Accept:** every listed task exists in that module; each task’s effects ⊆ agent effects (S12).
- **Writes:** `agent …` in `spec/<module>.gopyt`. Optional `evolve { max 4 timeout_ms 30000 reservoir 32 }` if they want self-evolution (needs `model`, `observe`, `egress`).
- **Next:** Q060

### Q070 — acceptance tests

- **Ask:** Add an acceptance test for a public callable, or skip? Effects of task
  calls remain explicit and are inferred on the test.
- **Choices:** A. Add test  B. Done
- **Next:** A → Q071  B → Q080

### Q071 — test obligation

- **Ask:** Test name (`snake`) and which public callable. State inputs and the
  expected public value; effectful tests use the specified runtime services.
- **Accept:** callable exists; expected value is expressible with public types.
- **Writes:** `open needs_test_<test_name>` on that callable. Elaborator writes
  `test/` (E-D3).
- **Next:** Q070

### Q080 — freeze spec, hand off

- **Ask:** Spec complete? Implementation is **not** written in this interview.
- **Choices:** A. Freeze spec  B. Back to types (Q010)
- **Writes (A):** nothing new. Print the file list. Hand off to `docs/elaborator.md` (E-D then E-A), then `gopyt fmt`, `gopyt check` (lock repair in `docs/lockfile.md`), `gopyt test`.
- **Next:** END

Interview never writes `impl/`. Spec-only trees are not runnable; that is intentional (S1, D2).

---

## Name repair (all name inputs)

If the human/agent emits slop, do **not** guess:

| they type | you say |
|-----------|---------|
| `getUser` | illegal; need `get_user` |
| `HTTP` / `UserID` | illegal; need `Http` / `UserId` |
| `int` | illegal; need `i64` |
| `string` | illegal; need `str` |
| `dict` | illegal; need `map[K, V]` |
| `null` | illegal; need `T?` / `none` |
| `db` effect | illegal; need `database.read` or `database.write` |
| English requires | illegal; write `open <snake_id>` |

One repair, matching `GOPYT_E*` when the checker exists (S19).

## Question order (summary)

```
Q001 package
Q001d path deps
Q002 api / agent / both
Q010–Q017 types, enums, fields   (loop)
Q040–Q046 traits, provide, Json
Q020 failure types
Q030–Q037 fn/task/workflow       (loop)
Q050–Q051 http                   (if api)
Q060–Q061 agent                  (if agent)
Q070–Q071 tests                  (loop)
Q080 hand off to impl elaborator
```

No questions about folders (`utils/`), GC, or syntax sugar. Layout is determined by module paths (S2–S3).
