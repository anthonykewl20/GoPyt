"""Compare incidental recursive depth acceptance for host and owned JSON output."""
import gc
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from gopyt import gobyte, jsonc
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.toolchain import TOOLCHAIN

rows = []
for depth in (100, 900, 950, 970, 980, 985, 990, 995, 1000, 1100):
    value = 1
    art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_I64)])
    for index in range(depth):
        value = [value]
        art.texprs.append(gobyte.TExpr(gobyte.TE_LIST, a=index))
    row = {'depth': depth}
    for mode in ('host', 'owned'):
        budget = ResourceBudget(ResourceLimits(256 * 1024 * 1024, 0, 0, 0))
        result = None
        try:
            result = jsonc.encode(art, value, depth, budget=budget if mode == 'owned' else None)
            assert result == '[' * depth + '1' + ']' * depth
            row[mode] = 'accepted'
        except RecursionError:
            row[mode] = 'RecursionError'
        finally:
            result = None
        gc.collect()
        assert budget.snapshot()['active_reservations'] == 0
    rows.append(row)
print(json.dumps({'runtime': TOOLCHAIN, 'python': sys.version,
                  'recursion_limit': sys.getrecursionlimit(),
                  'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'cases': rows}, indent=2))
