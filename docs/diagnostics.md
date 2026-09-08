# GoPyT v0 diagnostics

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Must follow: `docs/spec.md` S19. Agents key on **code**, not English. One repair, or no repair (`open`).

Wire format (stdout, exactly one error per invocation):

```
GOPYT_E013 missing_use
file: spec/orders.gopyt
line: 12
repair-bytes: 40

use payments.stripe { Payment, charge }
```

The first line is code, one space, id. `file` is optional and package-relative
with `/`; `line` is optional and 1-based. Runtime E101 adds `trap: N` after those
fields. `repair-bytes` is always present and is the decimal count of UTF-8 bytes
after the following blank line. Zero means no repair and no following bytes. The
repair bytes are exact replacement text (possibly a full file) and are not
terminated by an extra diagnostic newline. Diagnostics never include prose,
color, suggestions, stacks, or host paths.

The checker reports one actionable error per invocation, in deterministic
compiler traversal order within the phases specified by the closure amendment.
It does not promise a global sort of every latent diagnostic. Re-running after
each repair exposes the next error. The phase order matches the checker; unused
imports that require semantic resolution are checked after callable bodies.

Unknown codes are themselves `GOPYT_E001 internal`. Do not invent codes at runtime.

| code | id | meaning | repair |
|------|-----|---------|--------|
| E001 | internal | compiler bug | none |
| E002 | tab | tab character | replace tabs with 4 spaces |
| E003 | encoding | not UTF-8 or not `\n` | rewrite file UTF-8 LF |
| E004 | unformatted | not canonical fmt | run `gopyt fmt` |
| E010 | lex | unexpected character | delete/replace that character |
| E011 | parse | grammar mismatch | the one production that fits (see grammar.ebnf) |
| E012 | module_header | missing/mismatch `module` vs path, or module path uses a stdlib prefix (`core`, `net`, `data`, `store`) | first line `module <path>` that this package may own |
| E013 | missing_use | foreign name without allowlist | exact `use path { Name }` line |
| E014 | unused_use | `use` or allowlist name unused | delete it |
| E015 | use_star | wildcard / `as` / `from` | rewrite as E013 form |
| E016 | ident | illegal spelling (case, one letter, acronym shout) | the single S5 form |
| E017 | keyword | reserved word as name | pick a legal `snake`/`pascal` |
| E018 | extra_impl | impl file with no spec twin | delete extra file |
| E019 | extra_spec_item | impl public task/fn not in spec | add spec header or make it impl-private with a new name |
| E020 | unknown_type | type not in scope | declare it or `use` it |
| E021 | type_mismatch | expected T got U | convert explicitly or change the expr |
| E022 | arity | wrong argument count | the signature’s param list |
| E023 | missing_field | record/enum payload missing field | add `field: expr` |
| E024 | extra_field | unknown field | delete it |
| E025 | union | value not in declared union | add variant to spec return type or change value |
| E026 | optional | used `T?` as `T` | `match` on `some`/`none` |
| E027 | coerce | implicit conversion | named convert / `FromStr` / `Json` |
| E028 | builtin_shadow | user type named like builtin | rename |
| E029 | generic | wrong type args | `list[T]` / `map[K, V]` form |
| E030 | unresolved | `unresolved id` still in impl | replace statement with a body |
| E031 | spec_body | body in `spec/` | move body to `impl/` |
| E032 | impl_header | spec/impl header drift | copy spec header into impl |
| E033 | missing_impl | spec fn/task/workflow/provide has no impl | elaborator repair: canonical impl file |
| E034 | spec_unresolved | `unresolved` in spec | delete; use `open id` on contracts only |
| E035 | visibility | private type in public signature | move type to spec |
| E036 | rebind | name bound twice | new name |
| E037 | method | `value.fn(` call | `Trait.fn(value)` or `module.fn` |
| E038 | comma | comma in a field/pattern list (illegal there) | delete comma |
| E039 | wildcard | `_` in match | name every variant |
| E040 | toolchain | lock toolchain mismatch | set `toolchain` to this compiler id |
| E041 | lock_stale | lock missing/different | write diagnostic’s full lock text to `gopyt.lock` |
| E042 | dep_cycle | cyclic `[deps]` | remove a dep |
| E043 | dep_missing | path does not exist | fix `path` |
| E044 | dep_name | toml key ≠ dep `name`, or a module’s first path segment is a declared dep name | rename key or module |
| E045 | dep_dup | same dep name two paths | keep one |
| E046 | toml | illegal `gopyt.toml` key/range/git | see lockfile.md |
| E050 | effect_missing | call needs an effect not listed | add that effect to the `task` |
| E051 | effect_unused | listed effect never used | delete it |
| E052 | effect_unknown | not in S9 table | use a catalog name |
| E053 | fn_task | `fn` called a `task` | make it a `task` or remove the call |
| E054 | parallel | missing/illegal `max` or `timeout_ms` | `parallel max N timeout_ms M` with N,M > 0 |
| E055 | parallel_type | arm types differ | make every arm type `T` |
| E060 | contract_type | `requires`/`ensures` not `bool` | bool expr |
| E061 | contract_f64 | `f64` in contract | remove; `open id` if needed |
| E062 | contract_task | contract called a `task` | only `fn` |
| E063 | open_id | illegal `open` id | `open snake` |
| E064 | english_contract | prose where expr required | bool expr or `open snake` |
| E065 | open_hole | unresolved `open`, or missing/duplicate obligated test | fill the contract or add the exact E-D3 test |
| E069 | ffi_app | app listed `ffi` | remove `ffi`; use stdlib only |
| E070 | match | inexhaustive match | add missing arm |
| E071 | if_expression | `if` used where a value is required | use exhaustive `match` |
| E072 | for | `for` not over `list`/`range` | `for item in core.list.range(...)` |
| E073 | return_path | reachable function end without `return` | add explicit returns on every path |
| E074 | unknown_name | value/callable is not declared or imported | none; intent must identify the symbol |
| E080 | provide_orphan | `provide` for a type this module does not own | move provide |
| E081 | provide_dup | second provide for (Trait, Type) | delete one |
| E082 | provide_sig | member does not match trait | copy trait signature |
| E083 | provide_effect | provide task effects not ⊆ trait | shrink provide or widen trait |
| E084 | trait_fn | `fn` called effectful trait member | call from a `task` |
| E090 | http_verb | illegal verb | `get` `post` `put` `patch` `delete` |
| E091 | http_handler | route task missing/wrong module | handler `task` in this module |
| E092 | http_param | `{name}` not a `str` / `FromStr` param | rename or `provide FromStr` |
| E093 | serve | `net.http.serve` outside `task serve` + `http` | only that body |
| E094 | agent_task | agent `tasks` list mismatch | exact set of sibling tasks |
| E095 | agent_effect | task effects not ⊆ agent | add agent effect or shrink task |
| E096 | stdlib | `use` of non-stdlib / unknown stdlib name | S21 / stdlib.md |
| E097 | json | encode/decode without `Json` | `provide Json for Type` |
| E098 | from_str | path/value needs `FromStr` | `provide FromStr for Type` |
| E099 | f64_eq | `==` / `assert_eq` on `f64` | do not compare `f64` |
| E100 | opcode | unknown bytecode | rebuild with this toolchain |
| E101 | trap | VM trap (see bytecode.md codes 1–14) | fix source; not a result union |
| E111 | egress | missing/invalid `egress` or origin not listed | add the exact normalized origin |
| E112 | secret_leak | opaque `Secret` used as str/bytes/JSON/log | explicitly declassify with `core.secret.reveal` |
| E110 | dynamic_code | `eval`/`exec`/`system`/`popen`/`unsafe`/`shell` | delete; no dynamic code |
| E114 | evolve_model | `evolve` without agent `model` | add `model` or remove `evolve` |
| E115 | evolve_gate | candidate failed `gopyt check` | discard; do not apply |
| E116 | evolve_hot | patch live bytecode | restart on the new digest |
| E117 | observe_bound | reservoir/max not > 0 | set positive integers |

Trap numbers in `docs/bytecode.md` stay 1–14. They surface as `GOPYT_E101 trap` plus `trap: 3` (overflow, etc.).

Closure amendment: E098 also rejects an override of compiler-derived FromStr.
E117 also rejects nonrepresentable evolve bounds and conflicting policies for
multiple evolve agents in one module.
