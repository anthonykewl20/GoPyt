# Exact fixed-point monetary values

This normative extension to the closed standard library adds `core.money` for
architecture issue #17. Existing numeric operators are unchanged. Money is a
nominal record with `units: i64`, `scale: i64`, and `currency: str`; its value is
`units * 10^-scale` in the named currency. Scale is 0..18. Zero has no negative
sign. Currency is exactly three uppercase ASCII letters. This is a caller-assigned
identifier, not a claim that the code belongs to a particular currency registry.
Applications must choose valid business currencies, scale and rounding policy.
No exchange rate, default currency or mandated tax/payment rounding is inferred.

For domain errors in otherwise typed calls, all eight money functions return `core.status.ConvertError` on invalid input,
mismatch, required-but-disallowed rounding, division by zero or coefficient
overflow. General VM traps (including nonfinite host admission) still take precedence.
They never wrap, saturate, convert through floating point or silently
change scale/currency. All record fields are checked on every operation, including
records constructed directly by source code, JSON derivation or a host. Structural
record construction itself does not certify these domain invariants. Language
arithmetic operators do not accept Money; structural equality remains the normal
record equality and includes scale/currency. `compare` provides monetary ordering
only after checking equal currency and scale, returning -1, 0 or 1.

## Operations and bounds

- `make(units, scale, currency)` validates the three fields.
- `parse(text, scale, currency, rounding)` accepts at most 80 ASCII characters,
  matching `-?(0|[1-9][0-9]*)(\.[0-9]{1,36})?`. Whitespace, plus signs, grouping,
  leading integer zeros, exponents and nonfinite values are rejected. Decimal
  text is converted to an exact integer ratio and rounded once to the requested
  scale. Trailing fractional zeros are accepted; `Exact` permits them when the
  value is exactly representable. Overflow is tested after rounding.
- `format(value)` emits the exact decimal amount, with precisely `scale`
  fractional digits, no exponent, no grouping and a minus only for negative
  units. It omits currency; transport it with the currency field. Format/parse
  with the same scale/currency and `Exact` round-trips every valid Money.
- `add` and `subtract` require equal scales and currencies. They perform checked
  coefficient arithmetic and preserve those fields.
- `rescale` explicitly changes scale, rounds once, and preserves currency.
- `multiply_ratio(value, numerator, denominator, rounding)` accepts signed i64
  operands and a nonzero denominator. It applies the exact ratio to the amount,
  rounds once at the existing scale and preserves currency. This supports integer
  quantities, rates and allocations without inventing money-times-money units.
  Per-line versus aggregate rounding remains an explicit application decision.

Inputs and scale bound the size of all intermediate integers. The implementation
may use wider temporary integers so a representable final result is not rejected
merely because a multiplication intermediate exceeds i64. Persisted/output units
always lie in `[-2^63, 2^63-1]`. There is no implicit conversion from/to `f64`.

## Rounding

Every precision-changing API takes a `Rounding` enum; there is no ambient context.
`Exact` returns an error if any nonzero remainder would be discarded. `HalfEven`
chooses the nearest coefficient, with a tie choosing even. `HalfAway` chooses the
nearest coefficient, with a tie going away from zero. `TowardZero` truncates.
`Floor` chooses toward negative infinity; `Ceiling` toward positive infinity.
For example, rescaling -1.255 from scale 3 to 2 gives -1.26 for HalfEven,
HalfAway and Floor, -1.25 for TowardZero and Ceiling, and an error for Exact.
Rescaling -1.245 with HalfEven gives -1.24.

These directions agree with the [Python decimal reference](https://docs.python.org/3.11/library/decimal.html#rounding-modes),
used as an independent test oracle. Reference source was inspected at
`python/cpython@823f0323ee6ec1402088b73bce1a38473cac36dc`,
`Lib/_pydecimal.py` rounding helpers and `LICENSE` (PSF license history).
Leitir reported sampled verification. No reference code was copied or executed;
the implementation uses integer quotient/remainder, not Decimal. No runtime
dependency was added. Tests use the installed Python standard-library Decimal.

## Serialization and scope

For language-to-language storage, the normal typed record representation retains
units, scale and currency. For an external JSON consumer with an inexact numeric
parser, transfer `format(value)` as a string together with scale and currency,
and parse explicitly with `Exact`; do not send a binary floating-point amount.
These APIs do not add currency exchange, a registry, accounting policy, automatic
allocation of rounding residue, arbitrary precision decimals or calendar/time
semantics. Typed time and clock-anomaly qualification under #17 remain separate.

[Validation and full retail replay](../validation/money/README.md) retains the
independent oracle checks, source identities, initial failure and passing runs.
