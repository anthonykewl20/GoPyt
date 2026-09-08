# Order lifecycle contract v1

Frozen development contract, 2026-09-05. This is a bounded foundation, not a
measured agent campaign or a large-repository claim. Changes require a new
contract version and explicit amendment before any measured trial.

## Domain and transport

One scenario models one order of one SKU against available warehouse stock.
The application is a pure transition engine; the host adapter only decodes,
invokes compiled GoPyT, and encodes. No database, payment provider, HTTP service,
concurrency, or durability is claimed. Inputs are sequential commands.

JSON-lines request: exactly `stock` and `commands`. Stock is an integer 0..10000;
commands is a list of 0..100 commands. Every command has exactly `op`, `quantity`,
`unit_cents`, `member`, `expected_version`. Op is a string, member a bool, other
fields integers -1000000000..1000000000. Booleans are not integers. Unsafe numeric
spellings/ranges and invalid JSON/shape/type are transport errors: adapter exits
nonzero, never fabricates a business reply. Business-invalid fields inside the
transport domain produce the business outcomes below. Unused fields are ignored.

Response: exactly `results`, a list of snapshots, one per command. Snapshot keys
in declaration order: `outcome`, `status`, `version`, `stock`, `quantity`,
`refunded_quantity`, `net_cents`, `tax_cents`, `shipping_cents`, `charged_cents`,
`refunded_cents`. All amounts and counters are integers, outcome/status strings.
Initial state: status `empty`, version 0, stock as supplied, all other numbers 0.
Successful changes increment version exactly once; failures preserve every state
field. Rejected commands still emit a snapshot with their failure outcome.

## Precedence and state changes

1. Unknown op (anything except place/pay/ship/cancel/refund) => `invalid_command`.
2. For known ops, expected_version unequal to current version => `conflict`, even
   when business fields or state are invalid.
3. Check legal source state => `invalid_state` before validating business fields.
4. Check fields, then stock, then apply the change atomically in the pure state.

- `place`: only empty. Quantity 1..100 and unit_cents 0..1000000; otherwise
  `invalid_order`. If quantity exceeds stock => `out_of_stock`. Reserve quantity
  immediately (stock decreases); status `reserved`. Gross = quantity*unit_cents.
  Member discount = floor(gross*500/10000), otherwise 0. Net = gross-discount.
  Tax = floor(net*825/10000). Shipping = 0 if net >=10000 else 500. Save these
  quoted amounts; charged/refunded remain 0. Outcome `ok`.
- `pay`: only reserved. Charge net+tax+shipping; status `paid`; outcome `ok`.
  There is no external payment call, decline policy, or retry/idempotency key.
- `ship`: only paid. Status `shipped`; stock remains reserved/removed; outcome ok.
- `cancel`: only reserved or paid. Return entire quantity to stock. If paid,
  refunded_cents becomes charged_cents and refunded_quantity becomes quantity;
  if reserved both remain 0. Preserve quoted amounts and charged amount. Status
  `cancelled`; outcome ok. Cancelled is terminal.
- `refund`: only shipped or partially_refunded. Quantity 1..(original quantity
  minus refunded_quantity), else `invalid_refund`. Increase cumulative refunded
  quantity. Cumulative refunded_cents = floor((net+tax)*cumulative_quantity /
  original_quantity). Shipping is never refunded by this operation. Return the
  newly refunded quantity to stock. Status `refunded` at full quantity, otherwise
  `partially_refunded`; outcome ok. Refunded is terminal. Computing cumulative
  entitlement ensures splitting refunds never changes the total refund.

Snapshot status is an exhaustive typed internal category exposed as these exact
strings; outcomes likewise. Public GoPyT spec declarations precede bodies.
Suggested meaningful modules: model, validation, pricing, inventory, placement,
payment, fulfillment, refunds, service, presentation (implementation may group
closely related responsibilities). Do not add filler modules to inflate size.

## Acceptance and independence

An independent Python oracle is authored from this document, without reading
GoPyT bodies. It evaluates sequences, literal hand-calculated sentinels, seeded
boundary/random commands and deliberate wrong-policy mutations (tax basis,
shipping threshold, conflict precedence, refund rounding, restock, failure
mutation). Public examples are separate from controller acceptance. This oracle
is development-visible; it cannot later be called withheld without a new frozen
corpus and a verified boundary. Agent contributors share a model family and
workspace; independence is a division of authorship, not organizational isolation.
