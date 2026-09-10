"""Finite large-number equivalence/timing evidence; not a performance guarantee."""
import json
import sys
import time
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_json import integer_token

previous = sys.get_int_max_str_digits()
rows = []
try:
    sys.set_int_max_str_digits(0)
    for size in (6001, 20000, 100000):
        source = '-' + ('1234567890' * ((size + 9) // 10))[:size]
        start = time.perf_counter()
        oracle = json.loads(source)
        oracle_seconds = time.perf_counter() - start
        budget = ResourceBudget(ResourceLimits(size * 32, 0, 0, 0))
        start = time.perf_counter()
        result, end = integer_token(source, 0, budget)
        seconds = time.perf_counter() - start
        assert result == oracle and hash(result) == hash(oracle)
        assert end == len(source)
        peak = budget.snapshot()['peak']['native_bytes']
        del result
        assert budget.snapshot()['active_reservations'] == 0
        rows.append(dict(digits=size, seconds=seconds, oracle_seconds=oracle_seconds,
                         peak_native_bytes=peak, equivalent=True, released=True))
finally:
    sys.set_int_max_str_digits(previous)
print(json.dumps(dict(python=sys.version, original_digit_limit=previous, cases=rows), indent=2))
