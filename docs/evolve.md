# GoPyT v0 self-evolution

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Status: normative. Prior art: [Hermes Agent Self-Evolution](https://github.com/NousResearch/hermes-agent-self-evolution) (traces → candidates → gates → ship). GoPyT does **not** embed DSPy/GEPA or `eval`.

See `docs/hardening.md` for analysis, sketches, and hardening. Evolution **consumes** that report; it does not replace it.

## Loop

```
real gopyt run traces + core.observe.report
    → CUSUM/SPRT says "change" (hardening.md)
    → core.evolve.propose   (* at most one in flight *)
    → candidates in evolve/<digest>/
    → gopyt fmt + gopyt check + S30
    → multiplicative-weights pick among **checked** survivors
    → new lock digest; next process start loads it
```

No hot-swap of live bytecode (`GOPYT_E116`). No synthetic eval sets.

## Source

```
agent BillingAgent
    effects { network, database.write, model, observe, secret }
    tasks { collect, refund }
    evolve {
        max 4
        timeout_ms 30000
        reservoir 32
    }
```

- `max` = candidates (Little’s law: in-flight evolve work ≤ `max`).
- `timeout_ms` = elapsed budget for preparation and apply, subject to the
  [deadline and cleanup rules](parallel-admission-amendment-2026-09-10.md#evolution-wave-deadlines-and-cleanup).
- `reservoir` = Vitter reservoir size for traces (hardening.md).
- Requires `model` on the agent (`GOPYT_E114`).
- Absent `evolve` → no self-modification.

`core.evolve.propose` is compiler-filled (E-D), legal only in that module.

```
module core.evolve

type NoChange { }
type EvolveError { message: str }
type Applied { digest: str }

task propose() -> Applied | NoChange | EvolveError
    effects { model, time, log, observe, filesystem.read, filesystem.write }
```

Filesystem writes are the candidate tree + atomic apply, still sandboxed.
