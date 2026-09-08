# Multi-line order lifecycle example

A pure GoPyT transition engine implements the frozen [multi-line order lifecycle
contract v1](../../benchmarks/orders_multi/CONTRACT.md). One request contains a
per-SKU stock object, and up to 100 sequential commands drive one order with up
to five lines of different SKUs and tax categories. Placement quotes every line
with the placing customer's tier discount and the line's category tax, charges
line-count shipping unless gold/FREESHIP/threshold waives it, retains the tier,
and later supports payment, shipment, cancellation (full restock, full refund of
a paid order) and per-line cumulative refunds inside a 30-day window. All
arithmetic is integer cents with floor division of non-negative values; refund
entitlement is recomputed cumulatively per line so split refunds never change
the total.

This is a bounded development foundation: no database, provider calls, HTTP
endpoint, concurrency or durability. The independently authored acceptance
oracle is separate from this public example and its result is not claimed here.

The fifteen modules have matching public declarations in `spec/orders_multi/`
and bodies in `impl/orders_multi/`:

| Module | Responsibility |
| --- | --- |
| model | Status/Outcome/Operation/Tier/Category enums, Line/State/Command/Snapshot records, initial state |
| customer | Tier parsing and naming, tier discount rate and floor discount |
| tax | Category parsing, category tax rate and floor tax on the discounted net |
| pricing | Per-line quote (discount, net, tax) and the order quote (lines, subtotal, discount, tax sums) |
| shipping | Gold / FREESHIP / threshold waiver, else 500 + 100 per extra line |
| inventory | Per-SKU availability, reserve/restock with key-preserving contracts, all-line variants |
| lines | Line-list helpers: find, sums, unrefunded/all_refunded/entitled predicates, refund updates |
| validation | Operation classification, legal source statuses, placement field validation, refund quantity, refund status |
| window | Return window: day - day_placed in 0..30 |
| placement | Validate, price and reserve a multi-line order atomically |
| payment | Charge subtotal + tax + shipping from the saved quote |
| fulfillment | Shipment and reserved/paid cancellation with full restock |
| refunds | Per-line cumulative refund with window and quantity validation |
| service | Precedence, exhaustive dispatch, tier retention, sequential command processing |
| presentation | Exhaustive enum-to-string mapping into Snapshot records |

`orders_multi.service.run` is the request entry point. Every failure returns the
original state. `OrderAdapter(root: Path | None).run(value)` validates only the
transport envelope (exact key sets, SKU key pattern, 1..8 stock entries, 0..5
line objects, 0..100 commands, canonical integers in range, booleans are not
integers, duplicate keys rejected), constructs VM records and reads the declared
reply fields, emitting snapshot keys in the contract's order with `lines` as a
list and `stock` as an object in sorted key order.

From the repository root:

```bash
export PYTHONPATH="$PWD"
cd examples/orders_multi
python3 ../../scripts/gopyt fmt
python3 ../../scripts/gopyt check
python3 ../../scripts/gopyt test
python3 adapter.py <<'JSON'
{"stock":{"B":10,"A":5},"commands":[{"op":"place","lines":[{"sku":"A","quantity":3,"unit_cents":1201,"category":"standard"},{"sku":"B","quantity":2,"unit_cents":333,"category":"food"}],"sku":"","quantity":0,"expected_version":0,"day":10,"coupon":"","tier":"member"},{"op":"pay","lines":[],"sku":"","quantity":0,"expected_version":1,"day":10,"coupon":"","tier":"gold"}]}
JSON
```

The second snapshot is paid, version 2, tier `member` (the pay command's `gold`
is ignored), subtotal 4056, discount 213, tax 294, shipping 600 and charged
4950 cents, stock `{"A":2,"B":8}`. A transport error exits nonzero without a
business reply for that line; earlier valid lines may already have replies.

If source changes make the lock stale, `check` returns `GOPYT_E041` with the
full replacement text; `python3 ../../tools/repair_loop.py .` applies it.

## Executable obligations

Public declarations carry `ensures` postconditions, declared verbatim in
`spec/` and `impl/` and enforced by the VM on every executed call, for
precedence, failure immutability, version increment, stock-key preservation,
placement pricing and reservation, tier/day retention, payment, shipment,
cancellation, refund window, per-line cumulative entitlement, shipping never
refunded by refund, and refund status. List-based rules are expressed by
calling pure functions from the contracts (`orders_multi.lines.entitled`,
`all_refunded`, `refunded_total`, `subtotal`, `orders_multi.inventory.restock_all`).
`obligations.json` (schema `gopyt.obligations.v1`) gives each rule a stable id
(`OM-*`), records its representations (`ensures`, `preserved_field`, `test`,
`prose`), and separates two **proposed** policies (`OM-POL-B-*`) and one
**unresolved** request (`OM-POL-C-001`). See
[docs/obligations.md](../../docs/obligations.md) for the report commands:

```bash
python3 -m gopyt.project_context test examples/orders_multi --output /tmp/om-tests.json
python3 -m gopyt.obligations report examples/orders_multi --test-receipt /tmp/om-tests.json --output /tmp/om-obligations.json
```

Twenty-three public GoPyT tests (`test/orders_multi/`) cover hand-computed
multi-line quotes across categories and tiers, gold/FREESHIP/threshold
shipping, precedence, rejected-preserves-state, per-line split refunds with
exact cents, the day-30/31 window boundary, paid vs reserved cancellation,
terminal statuses and tier retention. Hand computations, failed attempts and
recorded adapter outputs are in [DEVELOPMENT.md](DEVELOPMENT.md).
