# Order lifecycle example

A pure GoPyT transition engine implements the frozen [order lifecycle v1
contract](../../benchmarks/order_lifecycle/CONTRACT.md). One request contains one
order, one SKU, initial stock and up to 100 sequential commands. Quotation,
reservation, optimistic version checks, payment, shipment, cancellation and
cumulative partial refunds all execute in compiled GoPyT bytecode. Integer cents
and cumulative refund entitlement make split refunds independent of rounding.

This is a bounded development foundation. It has no database, provider calls,
HTTP endpoint, concurrency or durability. It is not a large-repository benchmark
or evidence of agent effectiveness or runtime superiority. The independently
authored acceptance oracle is separate from these public examples.

The ten modules have matching public declarations in `spec/orders/` and bodies
in `impl/orders/`:

| Module | Responsibility |
| --- | --- |
| model | Immutable state, command/result records and typed categories |
| validation | Operation classification, legal source states and field bounds |
| pricing | Member discount, post-discount tax and shipping quote |
| inventory | Reserve and return stock with arithmetic contracts |
| placement | Validate and reserve a quoted order atomically |
| payment | Charge the saved quote |
| fulfillment | Shipment and reserved/paid cancellation |
| refunds | Cumulative refund entitlement and restocking |
| service | Precedence, exhaustive dispatch and sequential command processing |
| presentation | Exhaustive category-to-string mapping and snapshots |

`orders.service.run` is the request entry point. Its immutable recursion is
bounded by the transport limit of 100 commands. Every failure returns the
original state. `OrderAdapter(root: Path | None).run(value)` validates only the
transport envelope, constructs VM records and reads the declared reply fields.
It rebuilds/checks the package on construction; VM traps and compile errors
propagate instead of becoming business results.

From the repository root:

```bash
export PYTHONPATH="$PWD"
cd examples/orders
python -m gopyt fmt
python -m gopyt check
python -m gopyt test
python public_test.py
python adapter.py <<'JSON'
{"stock":3,"commands":[{"op":"place","quantity":3,"unit_cents":1201,"member":true,"expected_version":0},{"op":"pay","quantity":0,"unit_cents":0,"member":false,"expected_version":1}]}
JSON
```

The second snapshot is paid, version 2, stock 0, net 3423, tax 282, shipping
500 and charged 4205 cents. JSON-lines decoding rejects duplicate keys,
noncanonical integer spellings, floats/exponents, nonfinite numbers, out-of-range
values and invalid shapes/types. A transport error exits nonzero without a
business reply for that request; earlier valid lines may already have replies.
Unused command fields must still satisfy the transport types/ranges.

If source changes make the lock stale, `check` returns `GOPYT_E041` with the full
replacement text. Write that exact repair to `gopyt.lock`, then run `check` again.
Do not bypass checks or reuse old artifacts.

## Executable obligations (amendment 2026-09-06)

The public declarations now carry `ensures` postconditions for the contract's
precedence, failure-immutability, version, pricing, placement, payment,
cancellation, refund-entitlement and restocking rules, declared verbatim in
`spec/` and `impl/`. They are enforced by the VM on every executed call and trap
(code 2) on violation. `obligations.json` gives each rule a stable id
(`ORD-*`), records how it is represented, and separates it from **proposed**
maintenance policies (`POL-B-*`, the reliability benchmark's task B, which is
not this application's behavior) and an **unresolved** request (`POL-C-001`).
See [docs/obligations.md](../../docs/obligations.md) for the report and impact
commands. The amendment added no request/response fields and changed no
business behavior: development acceptance
`benchmarks/order_lifecycle/evidence/development-04/` passed all 3,908 scenarios
and 28 transport probes on the amended sources. Six public GoPyT tests were added
(15 total). The historical counts below describe the original 2026-09-05 record.

Public validation (2026-09-05 record): nine GoPyT tests and nine Python tests, including exact
snapshots, precedence/failure immutability, cancellation, refund rounding,
transport failures and the 100-command boundary. The Python suite compiles a
fresh temporary copy; JSON-lines probes launch that copy's adapter. The recorded
fresh-copy `fmt`, `check`, `test` and Python public-test commands all exited 0.
See [development-validation.json](development-validation.json) for actual command
outputs and [DEVELOPMENT.md](DEVELOPMENT.md) for failures and handoff details.
