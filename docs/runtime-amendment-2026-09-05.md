# v0 implementation closure amendment — 2026-09-05

Status: normative amendment, authorized by the request to address all audit
findings. This explicitly amends the conflicting clauses in bytecode.md,
hardening.md, evolve.md, elaborator.md, grammar.ebnf and diagnostics.md.
It retains charter D12's tracing collector; it does not waive missing work.

## Artifact and local lifetimes

Toolchain `gopyt-0.1.000` emits bytecode version 2. Version 1 artifacts must be
rebuilt. After the egress table is an evolve table: u32 count, then entries
(module str constant u32, max u32, timeout_ms u32, reservoir u32), sorted by
module. Bounds are positive. Multiple evolve agents in one module must specify
identical bounds; otherwise E117. The process reservoir uses the largest K. All three bounds must fit `1..2147483647`.

`RESET_LOCAL` (0x22, u16 slot, no stack effect) ends a non-parameter slot's
activation, clears its reference and initialization bit. The loader rejects
parameter/out-of-range resets. Compiler loop stores are preceded by resets,
including temporaries and nested loops. STORE_LOCAL always traps 10 on an
initialized slot. Source bindings remain immutable within each activation.

## Heap

The VM owns a nonmoving mark-and-sweep heap for records, enums, option payloads,
strings, bytes, lists, maps and secrets. Python objects are storage cells, not
the authority deciding language reachability. Roots are active frames,
constant pools, native arguments/results, scheduler captures/results and
explicit embedding handles. Collection occurs at instruction safepoints under
a shared mutator lock. Native foreign memory remains outside the heap; native
results are adopted when crossing back into the VM. Sweeping severs dead
container edges as well as releasing cells, including cycles.

## Bounded hardening

The limiter stores at most 4096 SHA-256 key digests. Keys are process-global.
Capacity/refill parameters are immutable for a key until its bucket is fully
replenished. Mismatched parameters return Throttled. Only fully replenished
idle entries can expire; pressure never resets a partially depleted bucket. An earliest-expiry lower bound avoids scanning the table on each rejected new key.
If there is no safely reclaimable entry, a new key returns Throttled. There is
no eviction that grants fresh tokens to an exhausted key.

Telemetry has an 8192-bit, four-hash Bloom filter, existing fixed sketches,
and K compact reservoir rows. Each row contains a task/tag capped at 256 UTF-8
bytes and an i64 code. Oversized user tags are represented by a digest. No
arguments, results' fields, keys or secret payloads enter traces. Saturating
u64 counters bound counter storage. Runtime outcome tags use nominal type or
enum variant names. The closed failure-name set is Denied, Throttled,
NotFound, ConvertError, DbError, IoError, HttpError, ModelError, ListenError,
EvolveError; other nominal outcomes are informational, not guessed failures. E112 is a compile-time diagnostic and therefore cannot be an event in a VM that never ran; the defensive native Secret guard records a payload-free deny tag.
Only task/workflow and effectful native calls count as task outcomes. Observe natives do not observe themselves, avoiding self-generated measurement events.

`build/traces` is an atomic JSON snapshot on CLI completion, capped at K rows;
it is not an append log. A failed optional dump does not change task results.
Tag/trap replay feeds the same recorded contract-trap indicators to a fresh
CUSUM. A candidate must not worsen its peak CUSUM. Because replay has no
payloads, equal fitness is expected and does not prove better business logic.
Candidate strategies have bounded persistent multiplicative weights; check or
replay failures halve a strategy's weight, survivors retain it; highest weight
wins, ties use strategy order. No model or external effects are run in replay.

## Evolution transaction

Evolution stages checked source and lock bytes, takes an exclusive package
transaction lock, verifies the baseline, writes/fsyncs an undo journal before
any replacement, replaces files then lock, and durably marks commit. Before
loading source, tools acquire the same lock and recover an incomplete journal
by rollback; committed journals are cleaned. Cooperating readers observe the
complete old or complete new package. Unrelated editors do not obey advisory
locks: observed divergent bytes cause refusal instead of destructive recovery. This does not provide compare-and-swap against an editor racing between the final byte check and replacement; such editors must coordinate through the lock. Recovery
is idempotent. The timeout bounds preparation; commit is a finite local durable
transaction and cannot promise hard real-time filesystem latency.

## Authoring and diagnostic ordering

The parser's documented soft keywords are legal in identifier positions:
`bool i32 i64 u32 u64 f64 str bytes list map unit get post put patch delete
test http evolve tasks max timeout_ms reservoir`. They retain their keyword
meaning in syntactic keyword positions. `reservoir` is part of the keyword set.

Formatting sorts existing import declarations without adding/removing names, and preserves expression evaluation order, literal
field order and match arm order, per the higher-priority formatter contract.
The elaborator computes minimal uses for generated text; fmt only lays out
existing syntax. Determined identity/constructor bodies require a structural
`ensures result == <parameter or constructor-of-parameters>` proof with matching
return type. Other bodies remain unresolved. Builtin Json/FromStr provides are
compiler-owned native derivations, with no user implementation body. Missing
required tests have a canonical E065 file repair with unresolved test bodies.

Diagnostics are fail-fast, in this deterministic phase order: package and
lex/parse; declaration/type collection and compiler derivations;
layout/import/declaration, agent/HTTP, egress and visibility checks; required-test
obligations; callable/contract/effect checking; missing-implementation
obligations; unused egress/imports; formatting; lock; bytecode emission/loading.
Within a phase the compiler's canonical traversal is authoritative. The tool
reports one actionable repair, not a global sorted list of all latent errors.

## Embedding and platform scope

`VM.call` retains the latest returned value per calling thread until its next
instruction safepoint/result handoff. Embedders retaining older values use
`with vm.heap.pin(value): ...`; `release_result()` ends the implicit handoff.
HTTP and parallel scheduler boundaries manage these roots internally.

The 0.1.000 runtime requires POSIX descriptor-relative operations and advisory file
locks. Linux is locally validated. macOS is in the CI matrix; Windows is not a
supported native runtime for this release. Configuring a workflow is not evidence
that it has executed. External model-provider availability and multi-day service
certification are not inferred from local TLS or load tests.
