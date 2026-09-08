# GoPyT Language Charter

Status: locked (D1–D14)  
Rule: later docs may not contradict this file without an explicit amendment.

“Open on purpose” lines under each D-section are historical (what the next question was). They are answered by later D-numbers and by `docs/spec.md`. The charter itself is closed.

This is the product contract for the language. It is not a syntax spec.

## Working definition

GoPyT is an AI-native, statically typed language for enterprise **APIs and agents**.

Humans manage **intent**: an agent interviews them; the artifact is a typed spec of architecture, contracts, business rules, and effects. Humans do not day-to-day read or write implementation.

The compiler and AI elaborate that spec into **canonical GoPyT**, check it, and run it on **our bytecode VM** with a simple tracing GC. `fn` is pure; `task` lists effects; concurrency is structured and bounded. Other languages exist only behind explicit FFI. Modules are public contracts; file layout is compiler-owned.

v0 is that loop plus enough HTTP/DB-or-FFI to demo a service and an agent. Not a native backend, registry, framework, or GC research project.

| ID | Decision | Locked choice |
|----|----------|----------------|
| D1 | Why it exists | AI-native systems language; AI owns code, humans manage intent |
| D2 | Human artifact | Typed intent spec, not implementation |
| D3 | First product | Backend APIs **and** agents |
| D4 | How it runs | Own interpreter / bytecode VM |
| D5 | Types | Static; local inference; explicit public boundaries |
| D6 | Effects | `fn` vs `task`; explicit effect lists |
| D7 | Concurrency | Structured, bounded parallelism |
| D8 | Interop | Explicit FFI boundaries |
| D9 | Structure | Modules = contracts; canonical file layout |
| D10 | v0 scope | Thin language + VM + spec loop; HTTP/DB-or-FFI |
| D11 | Spec contents | Architecture + contracts; elicited by an interviewing agent |
| D12 | Memory | Simple tracing GC |
| D13 | v0 security amendment | Static egress origins, opaque secrets, stdlib-only FFI |
| D14 | Observe + evolve in v0 | Bounded sketches + next-process checked digest; not live eval |

---

## Locked decisions

### D1 — Why this language exists

**Choice: A. AI-native systems language.**

GoPyT is a real general-purpose programming language for enterprise systems. It exists so that AI can build, understand, modify, expand, and operate the codebase at scale, while humans act as guiding managers rather than daily readers of source.

Context that is now assumed:

- More software will be generated than hand-written.
- Humans will often not see or read the implementation.
- Humans still own *what* is built, the architecture, and the business logic.
- AI owns the code: writing it, extending it, keeping it coherent as the system grows.

Therefore the language and compiler must make AI work reliable, not merely make source pleasant for humans.

What this decision rejects:

- A language justified only by “Python + Go + TypeScript” syntax taste.
- A language whose primary user is a human reading files all day.
- Building a Go analysis tool instead of a new language.

Implications (not yet designed, only constrained):

- There must be one canonical way to express ordinary programs, so AI does not invent parallel architectures for the same idea.
- Public contracts, types, effects, and architecture facts must be compiler-checkable, because humans will not catch drift by reading code.
- The compiler is the source of truth for “what the program is,” not an AI summary of the repo.
- Human-facing artifacts must carry intent (goals, architecture, business rules). Implementation source is for the machine and for rare audit, not for daily management.

### D2 — What humans actually write

**Choice: A. Intent spec, not implementation code.**

The human-facing source of truth is a typed intent spec: architecture, business rules, entities, APIs, invariants, and allowed effects.

The compiler and AI elaborate that spec into canonical GoPyT implementation. Humans manage the system by reviewing:

- spec diffs
- compiler reports
- tests
- running behavior

They do not manage the system by reading function bodies.

What this decision rejects:

- English-only prompts as the only contract (too ambiguous for enterprise).
- Humans owning public signatures while AI fills bodies, as the default workflow.
- Git/PRs over `.gopyt` files as the thing humans are expected to review daily.

Implications (not yet designed, only constrained):

- The language has two layers: **intent spec** (human) and **implementation** (machine).
- Implementation must be a deterministic, canonical elaboration of the spec plus project state, not a free-form AI dump.
- Spec and implementation must stay checkably in sync. If they drift, that is a compiler error.
- Implementation source may still exist on disk for execution, debugging, and rare audit. It is not the managerial interface.
- Natural language may feed the spec, but the spec is what the toolchain trusts.

Open on purpose: the spec’s exact format, and which enterprise workload we ship first, are later decisions.

### D3 — First product of the language

**Choice: C. Backend services/APIs and AI agents together from day one.**

v0 is one language for both:

- HTTP APIs, auth, databases, business workflows, jobs
- Agents, tools, tasks, parallel work, retries

Enterprise AI systems are both. The intent spec must be able to describe entities, endpoints, rules, effects, agent goals, tools, and permissions in the same model.

What this decision rejects:

- Shipping only APIs first and bolting agents on later as a library.
- Shipping only an agent DSL first and pretending it is a general enterprise language.
- Data pipelines / batch+streams as the v0 product.

Implications (not yet designed, only constrained):

- Core constructs must cover services and agents without two dialects.
- Effects/capabilities must express both “this handler writes to the ledger” and “this agent may call this tool.”
- v0 is larger than a single-workload language. Scope must be cut in other places (runtime, stdlib, syntax sugar), not by dropping one of these two domains.
- Data pipelines remain a later domain, not a v0 requirement.

Open on purpose: execution model (interpret vs compile vs transpile) is the next decision.

### D4 — How v0 programs run

**Choice: A. Own interpreter / bytecode VM.**

v0 execution path:

source / elaborated implementation → checker → bytecode → GoPyT runtime

GoPyT owns types, effects, concurrency, and replay. It does not borrow Python or Go as the program’s meaning.

What this decision rejects:

- Transpiling to Python as the language (the old prototype is not the path).
- Emitting Go (or another host) as the v0 meaning of a program.
- Shipping “syntax sugar over someone else’s runtime” and calling it a language.

Implications (not yet designed, only constrained):

- We must build a lexer, parser, checker, bytecode, and a small runtime before a native/WASM backend.
- Performance and deployment (single binary, containers) are secondary to owning semantics in v0.
- A later compiled backend is allowed. It must match the interpreter’s semantics, not replace them as the spec.
- Host interop (Python, HTTP, DB drivers) is FFI at a visible boundary, not the program’s core.

Open on purpose: the type system is the next decision.

### D5 — Type system

**Choice: A. Static types, local inference, explicit public boundaries.**

GoPyT is statically typed.

- Local implementation may infer types (`name = "Alice"` → `str`).
- Public APIs, spec entities, and module boundaries must declare types.
- No implicit coercions such as `"5" + 3`. Conversions are explicit; fallible conversions return typed outcomes.
- No null by default. Absence is `T?` or an algebraic variant.
- Ordinary failures are types (`User | NotFound | DatabaseError`), not unchecked exceptions. Matching is exhaustive.

What this decision rejects:

- Annotating every local binding (too noisy for a canonical implementation language).
- Gradual typing (untyped regions hide drift from humans who do not read code).
- Dynamic types with runtime checks as the main safety story.

Implications (not yet designed, only constrained):

- The checker is mandatory before bytecode.
- The intent spec uses the same type language as public implementation boundaries.
- Adding a new error variant to a public contract is a breaking change the compiler can see.
- Inference is a convenience inside closed bodies, not a way to hide the public shape of a system.

Open on purpose: effects (what code is allowed to do to the world) are the next decision.

### D6 — Effects

**Choice: A. Explicit effects plus `fn` vs `task`.**

- `fn` is computation. It does not touch the world.
- `task` may interact with the world. Its effects are listed and checked, e.g. `effects { network, database.write }`.
- HTTP handlers, jobs, tools, and agents are tasks.
- A `fn` cannot call a `task` unless those effects are allowed at the call site (in practice: they are not; `fn` stays pure).
- Effects are part of the public contract. Changing them is visible to the compiler and to the intent spec.

What this decision rejects:

- Capabilities-only as the v0 model (tokens for each resource, no effect types).
- A combined effect + capability system as a v0 requirement (allowed later, not required now).
- Convention, naming, or docs as the way we track side effects.

Implications (not yet designed, only constrained):

- The checker tracks a small closed set of effects (to be listed in the spec: network, filesystem, database, time, nondeterminism, etc.).
- Agent tool use and HTTP I/O are effects, not invisible runtime magic.
- Nondeterminism (time, random, model calls, external APIs) must appear as an effect so replay is possible later.
- Capabilities (which DB, which tool) can be added later without undoing this decision.

Open on purpose: concurrency is the next decision.

### D7 — Concurrency

**Choice: A. Structured, bounded parallelism.**

Concurrent work is a tree, not a bag of stray threads.

- A child runs only under a parent that waits for it (`parallel { ... }` or equivalent).
- Fan-out, timeouts, and cancellation are bounded and explicit.
- Failure belongs to the parent. Work does not leak after the parent returns.
- Execution is deterministic unless a listed effect introduces nondeterminism (time, network, model calls, etc.).

What this decision rejects:

- Go-style fire-and-forget goroutines as the default.
- Async/await coloring and “don’t forget to await” as the main model.
- A single-threaded v0 with no concurrency (incompatible with D3 APIs + agents).

Implications (not yet designed, only constrained):

- The VM scheduler implements structured tasks, not raw threads as the programmer model.
- Agent tool calls and HTTP handlers share this model.
- Unbounded `while spawn` is a compiler or runtime error, not a style issue.
- Replay (later) depends on this: a run is a tree of tasks plus recorded effects.

Open on purpose: interoperability with existing systems is the next decision.

### D8 — Interoperability

**Choice: A. Explicit FFI boundaries.**

GoPyT is the language of application logic. Other systems are reached through marked edges:

- HTTP, databases, Python, native libraries, model APIs
- Each crossing is an effect
- Types and contracts sit at the boundary
- Guarantees (purity, determinism, exhaustiveness) drop in a compiler-visible way on the other side

Source remains ordinary text files in Git.

What this decision rejects:

- Importing Python modules as if they were GoPyT (Python-first embedding).
- HTTP/JSON as the only v0 way to use existing code.
- First-class Python + JS + native hosts all in v0.

Implications (not yet designed, only constrained):

- Stdlib should cover HTTP and data access as GoPyT tasks with effects, not force every app to FFI immediately.
- Python interop is important for AI/ML, but it is a boundary, not the runtime.
- An AI must not be able to “just import” a dynamic Python module and erase the spec.
- Package migration from existing ecosystems is adapters at the edge, not mixed-language modules.

Open on purpose: modules, files, and packages are the next decision.

### D9 — Modules, files, and packages

**Choice: A. Modules are public contracts; file layout is canonical.**

- A module’s public surface is explicit: types, `fn`/`task` signatures, effects.
- Implementation hiding is the point. Callers (and AI) reason from the contract, not the body.
- The compiler owns a canonical file layout. Ordinary programs do not get `utils/` vs `helpers/` vs `common/` as competing styles.
- Packages are versioned modules with a lockfile. Builds are reproducible.
- Source is still files in Git (see D8). The layout is just not a matter of taste.

What this decision rejects:

- Files as the only architectural unit.
- Go/Rust-like packages where directory taste is left to convention.
- Hash-only, file-less source (Unison-style) as the system of record.

Implications (not yet designed, only constrained):

- Canonical formatter + canonical project layout are part of the language, not optional tooling.
- Spec-level architecture (services, agents, modules) must map onto this module system.
- Changing a public contract is the expensive change; changing a body is local (D1).
- AI-generated structure is elaborated by the compiler, not invented per prompt.

Open on purpose: v0 scope (what we refuse to build first) is the next decision.

### D10 — v0 scope

**Choice: A. Thin language + VM + spec loop.**

v0 is the smallest system that still matches D1–D9:

**In v0**

- Typed intent spec → checker → canonical implementation → bytecode → GoPyT VM
- Language core: `type`, `enum`, `fn`, `task`, modules, `match`/`if`/`for`/`return`, `parallel`
- Static types with local inference; explicit public boundaries
- Explicit effects; `fn` vs `task`
- Structured bounded parallelism
- Canonical file layout and formatter
- CLI: `check`, `run`, `fmt`, `test`
- Enough stdlib to demo APIs and agents: HTTP, a DB or FFI boundary, tests

**Out of v0**

- GC/memory research, LLVM/native/WASM backends
- Package registry
- Full web framework
- Theorem prover / optional formal verification
- Capability system (effects only)
- Data-pipeline stdlib
- Self-hosting the compiler
- Syntax sugar and branding work

What this decision rejects:

- A VM with no HTTP/DB story (too weak for D3).
- Shipping capabilities, replay, and a framework in the first cut.

Implications (not yet designed, only constrained):

- Replay, capabilities, and a compiled backend are compatible later; they must not block v0.
- If a feature is not needed to check, run, and demo one API service plus one agent from a spec, it is not v0.

Open on purpose: how detailed the v0 intent spec is is the next decision.

### D11 — What the intent spec contains (and who types it)

**Choice: A. Architecture + contracts, not algorithms.**  
**Authoring: humans usually do not write the spec file by hand.**

The spec is still the managerial source of truth (D2). What belongs in it:

- modules, services, agents
- entities and public APIs
- business rules as checkable invariants
- allowed effects
- tests and examples

What does not belong in it:

- loops, algorithms, storage layout, function bodies

If a rule cannot be checked, it is marked open. It does not silently become a comment in generated code.

How humans actually produce it:

- The default workflow is a conversation with an AI agent, like this charter process: the agent asks one question at a time; the human picks or describes; the agent writes the spec.
- Humans may edit the spec directly; that is not the expected daily path.
- Natural language is how intent is *elicited*. The spec is what the toolchain *trusts*.

What this decision rejects:

- Putting full control flow in the spec (humans coding again).
- A one-page brief from which AI invents architecture (humans no longer own architecture).
- Two spec languages in v0 (brief + contracts as separate products).
- Treating chat history as the source of truth instead of the spec artifact.

Implications (not yet designed, only constrained):

- v0 needs a spec format *and* an elicitation loop that can fill it.
- The compiler never compiles a prompt. It compiles a spec (+ elaborated implementation).
- Questionnaires/choices for architecture and business rules are part of the product, not a side chat.
- Implementation remains canonical and machine-owned (D2, D9).

### D12 — v0 memory

**Choice: A. Simple tracing GC.**

The v0 VM allocates values on a heap and collects garbage by tracing. The language has no lifetimes, ownership annotations, or region syntax in v0.

What this decision rejects:

- Reference counting as the v0 model (cycles would leak without extra work).
- Arena-per-request as the only heap (long-lived agent state needs a real heap).
- “Whatever the host GC does” as the language’s memory story.

Implications (not yet designed, only constrained):

- Pause times, generational/compacting collectors, and tuning are out of v0 (D10).
- FFI (D8) must treat foreign memory as a boundary: GoPyT objects are GC’d; foreign objects are not silently pulled onto our heap.
- A later memory model (regions, RC+GC hybrid) may replace this only if the language-level story stays “no lifetimes in ordinary code.”

---

## Charter closed

### D13 — Narrow v0 security amendment

S30 adds three closed safety mechanisms needed when agents own implementation:
a per-module static outbound-origin allowlist, an opaque `Secret` type with
explicit declassification, and a ban on application-defined FFI. This narrowly
amends D6/D8/D10. It is not the general resource-capability system deferred by
those decisions: there are no capability values, delegation, dynamic grants,
custom FFI declarations, or policy framework in v0. HTTP and the KV database use
the toolchain's fixed, typed native boundaries.

`core.secret.reveal` is compiler-visible declassification, not proof that the
returned `str` remains secret. Review and tests must treat every reveal as a
security-sensitive sink.

### D14 — Bounded observe and evolve in v0

D10 deferred full replay, a capability system, and a theorem prover. **S31–S32**
narrowly add:

- in-process streaming observe (fixed-size sketches + reservoir);
- `evolve` as **next-process** checked digest replace, not live `eval`.

This amends D10’s “no replay in the first cut” only for **tag/trap reservoir
replay** used as CUSUM input. It does not add capabilities, LLVM, a prover, or
hot-patched bytecode.

Empirical claims about detection and evolution require `docs/validation.md`
prototypes. They are not proven by this charter sentence.

---

D1–D14 are locked. Historical “next decision” lines above record the design
sequence and do not reopen the completed v0 documents.
