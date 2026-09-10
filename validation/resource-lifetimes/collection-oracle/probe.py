"""Frozen differential collection cases; elapsed times are diagnostic only."""
import gc
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_collections import range_list, set_map, map_keys
from gopyt.toolchain import TOOLCHAIN

CASES = (0, 8, 128, 1024, 16384)


def run():
    rows = []
    for count in CASES:
        budget = ResourceBudget(ResourceLimits(256 * 1024 * 1024, 0, 0, 0))
        expected = list(range(-1000, count - 1000))
        started = time.perf_counter()
        result = range_list(-1000, count - 1000, budget)
        elapsed = time.perf_counter() - started
        assert result == expected
        rows.append({'operation': 'range', 'count': count, 'seconds': elapsed,
                     'usage': budget.snapshot()})
        del result
        gc.collect()
        assert budget.snapshot()['active_reservations'] == 0
        source = {i: i * 2 for i in reversed(range(count))}
        expected_map = dict(source)
        expected_map[-1] = 99
        started = time.perf_counter()
        result = set_map(source, -1, 99, budget)
        elapsed = time.perf_counter() - started
        assert result == expected_map
        keys = map_keys(result, budget)
        assert keys == sorted(expected_map)
        rows.append({'operation': 'map_update_and_keys', 'count': count,
                     'update_seconds': elapsed, 'usage': budget.snapshot()})
        del result, keys
        gc.collect()
        assert budget.snapshot()['active_reservations'] == 0
    print(json.dumps({'runtime': TOOLCHAIN, 'python': sys.version,
                      'platform': platform.platform(),
                      'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'scope': 'named differential cases and diagnostic elapsed time; no production SLO claim',
                      'results': rows}, indent=2))


if __name__ == '__main__':
    run()
