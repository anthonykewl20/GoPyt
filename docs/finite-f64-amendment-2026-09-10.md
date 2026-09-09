# Finite binary64 values

This normative amendment clarifies S6/S21, the implementation guide, bytecode
constants and host/native admission. It addresses the floating-point portion
of architecture issue #17; exact money, richer time semantics and real-data
qualification remain open.

`f64` contains finite IEEE 754 binary64 values, including subnormals and signed
zero. NaN and either infinity are outside its domain. Decimal source literals
round to nearest, ties to even. Finite rounding is permitted: a sufficiently
small positive literal rounds to zero. A literal whose conversion overflows to
infinity fails with `GOPYT_E118 numeric_range` before artifact publication.
Use a representable literal; money requiring exact decimal semantics must not
be represented by `f64`.

The existing restrictions remain: literals use `digits.digits`; exponent
notation and unary minus on `f64` are unavailable. Arithmetic, ordering,
equality, contracts and JSON conversion do not gain floating-point support.
Host and bytecode values may contain negative finite numbers and negative zero.

All encoded and decoded `TAG_F64` constants must be finite binary64 values,
including unused constants. Python artifact construction requires an exact
`float` for this tag; an integer is not silently converted. Encoding, validation,
decoding and VM construction reject invalid constants with `GOPYT_E100`.

External VM calls and native calls check arguments before execution and arguments
and results after successful execution. A nonfinite float causes `TRAP_TYPE`
(code 9). Checks traverse language lists, maps (keys and values), records, enums
and optional values, and handle cycles without recursion. Mutable arguments are
checked again on subsequent admission. Internal compiled calls rely on validated
constants and these native boundaries; no floating-point arithmetic creates new
values inside compiled code.

These checks do not constitute general host type validation or isolation from
concurrent host mutation. A native's external side effects and argument mutation
are not rolled back when a post-call check traps. Opaque secrets are not traversed.

## Regression evidence

`python -m unittest gopyt.test_finite_f64 -v` checks overflowing source and preserved
prior output, exact decimal constructions with independently specified binary64
bits (maximum finite value, minimum subnormal and halfway underflow), raw unused
NaN/infinity constants, malformed in-memory constants, native return admission,
nested/cyclic graphs and repeated admission of host-mutated lists. This is a
boundary correctness claim, not a money/time or production performance claim.
