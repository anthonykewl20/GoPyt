# Development record — 2026-09-06

The author read `benchmarks/orders_multi/CONTRACT.md` (sha256
`2eb885a1303ddca443f38b0efe09c2c32c1706adcffa3200cf53c2a5f9837399`) and
`examples/orders` as the style reference. **No oracle, acceptance corpus or
evidence directory for this contract was read.** Independent acceptance is owned
by the controller and is **not claimed here**; this record reports public
development checks only.

Public declarations for all fifteen modules (`spec/orders_multi/`) were
written before any body. Runtime/compiler changes: none. Files owned:
`examples/orders_multi/` only.

## Design

- `State` is a record with scalar amounts, `tier: Tier`, `day_placed`,
  `lines: list[Line]` and `stock: map[str, i64]`. Every transition is a pure
  `fn` returning `Transition { outcome, state }`; failures return the input
  state unchanged.
- List rules are recursion over `core.list.get` with `some/none` matches (GoPyT
  `for` carries no accumulator). Helpers such as `subtotal`, `refunded_total`,
  `all_refunded`, `entitled`, `refund_line`, `refund_all`, `reserve_all` and
  `restock_all` live in `lines`/`inventory` and are called from `ensures`
  clauses, which is how the list-based contract rules are made executable.
- Precedence is in `service.transition`: unknown op, then version conflict,
  then the legal-status table, then dispatch. Field validation and stock live
  in `placement`; refund validation (sku exists, quantity range, window) in
  `refunds` (order does not affect the outcome, all yield `invalid_refund`).
- Every `State` constructor other than `placement.place` and `model.initial`
  copies `tier`, `day_placed`, `subtotal_cents`, `discount_cents`,
  `tax_cents`, `shipping_cents` from its `state` parameter so the
  `preserved_field` representations hold syntactically; `charged_cents` is
  additionally excepted for `payment.pay`.
- Paid cancellation writes `refunded_cents = charged_cents` (includes shipping)
  while each line gets `refunded_cents = net + tax`; the contract clause
  `refunded_total(result.state.lines) == subtotal + tax` documents that the
  line sum deliberately excludes shipping.
- The adapter mirrors `examples/orders/adapter.py` and adds the stock object,
  SKU key regex, line-object shape and the 5-line / 8-SKU limits.

## Hand computations used by the tests and probes

Rates: tier none 0 / member 500 / gold 1000 bp; tax standard 825 / food 200 /
exempt 0 bp; all floor division.

1. `gold_quote_across_categories` (gold, day 7): A 2×1999 standard: gross 3998,
   discount floor(399800/10000)=399, net 3599, tax floor(3599×825/10000)=floor(296.9)=296.
   B 3×450 food: gross 1350, discount 135, net 1215, tax floor(24.3)=24.
   C 1×12345 exempt: discount floor(1234.5)=1234, net 11111, tax 0.
   subtotal 15925, discount 1768, tax 320, shipping 0 (gold). Paid charge 16245.
   Paid cancel: line refunds 3895 / 1239 / 11111 (sum 16245 = charged because shipping is 0).
2. `member_quote_two_lines` (member, day 10): A 3×1201 standard: gross 3603,
   discount 180, net 3423, tax floor(282.4)=282. B 2×333 food: gross 666,
   discount 33, net 633, tax floor(12.66)=12. subtotal 4056, discount 213,
   tax 294, shipping 500+100×(2−1)=600 (4056 < 10000). Charged 4950.
   Refund A 1 of 3: floor(3705×1/3)=1235.
3. `none_tier_three_lines_pays_line_shipping`: 1×100 standard (tax 8),
   1×200 food (tax 4), 1×300 exempt (tax 0): subtotal 600, tax 12,
   shipping 500+200=700, charged 1312. Paid cancel: order refunded 1312,
   line refunds 108/204/300 (sum 612 = subtotal + tax).
4. `freeship_and_threshold_shipping`: FREESHIP → 0; 1×9999 none → 500;
   1×10000 none → 0; 1×10000 member → subtotal 9500 → 500.
5. `split_refund_across_lines_exact_cents` (none, day 100): A 3×1234 standard:
   net 3702, tax floor(305.4)=305, total 4007. B 2×500 food: net 1000, tax 20,
   total 1020. subtotal 4702, tax 325, shipping 600, charged 5627.
   Refund A1 → floor(4007×1/3)=1335; B1 → 510 (1845); A cumulative 2 →
   floor(8014/3)=2671 (3181); A3 → 4007 (4517); B2 → 1020 → 5027 = 4007+1020,
   status refunded, stock A 3 / B 2 restored, shipping 600 never refunded.
   Per-command rounding would have given 1335+1336+1336; cumulative gives
   1335, 2671, 4007.
6. `refund_window_day_30_inside_day_31_outside`: day_placed 5; day 35 (30 later)
   ok, day 36 invalid_refund, day 4 invalid_refund, day 5 ok.
7. `tier_retained_when_later_commands_say_gold`: member 2×3000 standard: gross
   6000, discount 300, net 5700, tax floor(470.25)=470, shipping 500, charged
   6670; pay/ship/refund commands say `gold` but tier stays member; full refund
   6170 = 5700 + 470.
8. `refunded_is_terminal`: 1×100 standard none: charged 100+8+500=608, full
   refund 108.
9. `run_sequences_snapshots`: 2×250 food none: net 500, tax 10, shipping 500,
   charged 1010.

## Failed attempts, in order (diagnostics verbatim)

1. `gopyt fmt` exited 1 with `GOPYT_E046 toml` on `gopyt.toml` line 1. The
   manifest had a `toolchain = ...` line copied from the reference lock file;
   the manifest loader accepts only `name` and `version`. Removed the line.
2. `gopyt fmt` exited 1 with `GOPYT_E011 parse`, `impl/orders_multi/refunds.gopyt:64`.
   The last `ensures` clause before the body ended in `Status.PartiallyRefunded`
   and the following `{` was parsed as a payload constructor (the same trap
   recorded in `examples/orders` for `if`). Rewrote the comparison as
   `Status.PartiallyRefunded == result.state.status` in spec and impl.
3. `gopyt check` exited 1 with `GOPYT_E004 unformatted`,
   `impl/orders_multi/customer.gopyt`, repair `discount_rate(...)`: a
   same-module call inside a contract must be the bare name, not
   `orders_multi.customer.discount_rate`. Stripped the self-module prefix in
   customer, tax, inventory, lines and pricing contracts.
   `tools/repair_loop.py` reported `GOPYT_E004: no applicable repair` because the
   repair bytes are a hint, not a file rewrite.
4. `gopyt check` exited 1 with `GOPYT_E014 unused_use` at
   `spec/orders_multi/pricing.gopyt:3` and then `spec/orders_multi/refunds.gopyt:3`.
   Cause (established by instrumenting `Checker.used_names` in a scratch
   script, no compiler edit): a name used only in a spec `ensures` clause is
   credited to the spec file only when the impl first marks it inside that
   contract; `Line` (pricing) and `Status` (refunds) were already marked used by
   impl helper signatures (`quoted_line -> Line`, `refund_status -> Status`)
   during the obligations phase. Fixes: dropped `Line` from the pricing spec
   `use` line (it appears only inside `core.list.len[Line]` in a clause) and
   moved `refund_status` to the public `validation` module, whose spec already
   imports `Status`. Renaming refund helpers so they sort after `refund` did not
   help and was kept only as a naming choice.
5. `gopyt fmt` exited 1 with `GOPYT_E017 keyword`,
   `impl/orders_multi/validation.gopyt:87`: the new `refund_status` clauses
   ended in `Status.Refunded` / `Status.PartiallyRefunded` before `{` (failure 2
   again). Flipped both comparisons.
6. `gopyt fmt` exited 1 with `GOPYT_E011 parse`, `test/orders_multi/service.gopyt:8`
   when probing whether a test module may declare an `fn` helper. It may not;
   the tests are generated as full record literals from a scratch script.
7. The first `project_context test` receipt was reported `stale` by the
   obligations report with reason `engine identity differs from current
   compiler`, although no compiler file was edited between the two commands.
   Regenerated the receipt after the registry was written; the second receipt
   was `current`. Retained as an observation.

No business assertion failed after the tests compiled. A deliberate mutation
(`inside(10, 41)` expected `true`) made `gopyt test` exit 2 with
`GOPYT_E101 trap`, `trap: 13`, confirming the harness reports assertion
failures; the mutation was reverted before the final checks.

## Exact commands and results (final sources)

```
export PYTHONPATH=/home/soultransit/devtony/gopyt
cd examples/orders_multi
python3 ../../scripts/gopyt fmt      # exit 0
python3 ../../scripts/gopyt check    # exit 0
python3 ../../scripts/gopyt test     # exit 0 (23 tests, silent on success)
cd ../..
python3 -m gopyt.project_context test examples/orders_multi --output <scratch>/tests-final.json      # exit 0
python3 -m gopyt.obligations report examples/orders_multi --test-receipt <scratch>/tests-final.json --output <scratch>/report-final.json   # exit 0
```

Report totals: 33 obligations (30 contract, 2 proposed, 1 unresolved),
`with_gaps` 3 (exactly the proposed/unresolved entries). 66 `ensures`
representations `bound` and each executed by passing tests, 7
`preserved_field` representations `holds`, 55 `test` representations
`passed` and every one reaches its obligated functions. Receipt status
`current`.

## Recorded adapter invocations

Probe A (member, two lines, pay, ship, refund on day 40 = 30 days later, then
a stale-version refund):

```
{"stock":{"B":10,"A":5},"commands":[place A 3×1201 standard + B 2×333 food, member, day 10, v0; pay v1; ship v2; refund A 1 day 40 v3; refund A 1 day 39 v3]}
-> outcomes ok/ok/ok/ok/conflict; snapshot 4: partially_refunded, version 4,
   tier member, subtotal 4056, discount 213, tax 294, shipping 600, charged 4950,
   refunded 1235, lines [A 3/1 net 3423 tax 282 refunded 1235, B 2/0 net 633 tax 12],
   stock {"A":3,"B":8}; snapshot 5 repeats snapshot 4 with outcome conflict.
```

Probe B (gold, three categories, pay, cancel, pay again): snapshots
reserved/paid/cancelled/invalid_state; charged 16245, refunded 16245, lines
refunded 3895/1239/11111, stock restored to 10/10/10, version stays 3 on the
rejected pay.

Probe C: `{"stock":{"A":1},"commands":[]}` → `{"results":[]}`; a 2×1000 exempt
order placed day 5 gives shipping 500, charged 2500; refund on day 36 →
`invalid_refund` (state unchanged), refund on day 35 → ok, refunded 1000,
partially_refunded, stock A 1.

Transport probes (each exit 1, no reply line): extra top-level key; duplicate
stock key; boolean stock value; float stock value; SKU key `A-1`; empty stock;
nine stock entries; stock 10001; ninth command key; line object missing
`category`; six line objects; quantity 1000000001; boolean quantity; `01`
integer spelling; non-JSON input; 101 commands. 100 commands of `pay` on an
empty order return 100 `invalid_state` snapshots at version 0 (exit 0).

## Package digest

`gopyt.lock` after the final check: see the `digest` line in
[gopyt.lock](gopyt.lock) — `sha256:dd3a5ac382f1ec6c3b53a1561c86aac075e3b2e9bdf2d3e9dee9f90107015ce3`
at the time of this record (documentation files are not part of the digest).

## Blind spots

Rules the `ensures` clauses cannot express and that rest on tests only:

- Precedence between `invalid_order` and `out_of_stock` (OM-PREC-004) and the
  full placement field table; the clause only states that an accepted order was
  valid and in stock.
- The refund lower bound `quantity >= 1` and "sku names a line" are enforced by
  the body; the contract's `entitled` predicate only bounds
  `refunded_quantity <= quantity`.
- That `refund_line` touches exactly one line (the contract equates the result
  with the helper's own output, so the helper is trusted).
- Terminal statuses (OM-TERM-001) are a property of the `legal` table, not a
  postcondition.
- Sequential processing in `run` (each command sees the previous result) has no
  contract; it is covered by `run_sequences_snapshots` and the adapter probes.
