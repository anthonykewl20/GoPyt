"""Finite Decimal integer-conversion allocation evidence, not an upper bound."""
import json
import sys
import tracemalloc
from decimal import Decimal
from gopyt.jsonc import _as_int, ConvertFail

cases = ['0.0', '-0.00', '1.0', '1.5', '9223372036854775807.0',
         '9223372036854775808.0', '18446744073709551615.0',
         '18446744073709551616.0', '-9223372036854775808.0',
         '1e1000000', '1e-1000000', '1.' + '0' * 100000]
rows = []
for index, source in enumerate(cases):
    value = Decimal(source)
    tracemalloc.start()
    result = failure = None
    try:
        result = _as_int(value)
    except ConvertFail as error:
        failure = error.message
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rows.append(dict(case=index, input_characters=len(source),
                     decimal_size=Decimal.__sizeof__(value), result=result,
                     failure=failure, traced_current=current, traced_peak=peak))
print(json.dumps(dict(python=sys.version, cases=rows,
                     scope='Input Decimal allocated before tracing; finite observations only'), indent=2))
