# Contract drift in a ticket lifecycle

GoPyT rejects an implementation that deletes or changes a public contract while
its spec stays unchanged. Try the complete example from the repository root:

```sh
python3 examples/ticket_contracts/demo.py
```

The script uses disposable copies and leaves this example untouched. It exits
zero only when all four expected behaviors occur:

| Case | `check` | `test` |
| --- | --- | --- |
| Matching spec and implementation | Pass | Pass |
| Delete the implementation's version postcondition | E032 | Not run |
| Weaken its version precondition from `>= 1` to `>= 0` | E032 | Not run |
| Change the increment from `+ 1` to `+ 2`, keeping contracts | Pass | Runtime postcondition trap 2 |

The public rule in [spec/ticket_contracts.gopyt](spec/ticket_contracts.gopyt):

```gopyt
fn next_version(version: i64) -> i64
    requires version >= 1 and version < 1000000
    ensures result == version + 1
```

The matching [implementation](impl/ticket_contracts.gopyt) adds the body. The
checker compares the clause contents, kinds and declaration order. Comments
and line numbers do not count as drift. It does not prove expressions
mathematically equivalent: preserve the declared expression when implementing
it. An implementation-only change to a bound is E032 even if the number of
clauses stays the same.

The lifecycle advances Open -> InProgress -> Closed, keeps priority unchanged,
increments version once, and rejects stale expected versions. This is a pure
teaching example, with no database, HTTP service or production ticket data.

## Use the protection in a project

1. Put the intended public signatures, effects and contracts in `spec/`.
2. Keep their matching headers in `impl/`; make routine body edits there.
3. Run `gopyt fmt`, `gopyt check`, then `gopyt test`. If E041 reports a stale
   lock, review and apply its exact lock replacement, then check again.
4. Resolve E032 by restoring the intended implementation header. A deliberate
   policy change requires review of the spec, matching implementation, and
   affected tests; do not weaken the spec merely to make a check pass.
5. Run the checks in CI with failure stopping the job. This repository's normal
   validation workflow also runs this four-case demonstration.

With GoPyT installed, check the unmodified example directly:

```sh
cd examples/ticket_contracts
gopyt check
gopyt test
```

The lock refresh in demo.py applies only to disposable mutated packages so a
stale digest cannot hide the contract diagnostic. It is not a new compiler
command or an automatic repair policy for your project.

## What remains your responsibility

Changing both spec and implementation to remove a rule can still pass. A
body that obeys the types but implements the wrong business policy can pass
`check`; tests and runtime contracts cover only the properties they execute.
Keep spec review and business acceptance checks as separate requirements.

The same-count contract-content check was repaired while building this example;
earlier local compiler snapshots only compared clause counts. See the
[repair evidence](../../docs/agent-evidence/2026-09-08-contract-content-fix/REPORT.md).
