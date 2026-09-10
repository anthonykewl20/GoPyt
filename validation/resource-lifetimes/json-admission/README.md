# JSON parser compatibility baseline

Frozen before parser implementation at 89a3917df9618dcd3101260dab9efa1cc99a7d48.
The probe records 12 accepted and 14 rejected cases on each pinned runtime.
Accepted values are compared with the standard JSON decoder using Decimal for
fractional/exponent values. Rejection cases freeze GoPyT's stricter duplicate-key
and Unicode-scalar behavior as well as malformed JSON and trailing data.

This finite baseline is not complete JSON conformance or resource qualification.
The new producer must retain Decimal behavior, including syntactically valid large
exponents, and leave typed numeric range rejection to typed conversion. It must
not silently replace decimal parsing with floating point or accept lone surrogates.
No parser behavior has changed yet. See docs/json-admission-design.md for allocation
sites and the overlapping generic/typed graph requirement.
