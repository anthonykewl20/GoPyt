"""Finite host/owned typed nesting comparison and cleanup observations."""
import json
import sys
from gopyt import gobyte, jsonc
from gopyt.resource_budget import ResourceBudget, ResourceLimits
rows = []
for depth in (10, 100, 300, 450, 500, 700, 900, 1000, 1200):
    art = gobyte.Artifact(texprs=[gobyte.TExpr(gobyte.TE_I64)] +
        [gobyte.TExpr(gobyte.TE_LIST, a=i) for i in range(depth)])
    source = '[' * depth + '1' + ']' * depth
    row = dict(depth=depth)
    for mode in ('host', 'owned'):
        budget = ResourceBudget(ResourceLimits(10000000, 0, 0, 0))
        result = failure = None
        try:
            result = jsonc.decode(art, source, depth,
                                 **(dict(budget=budget) if mode == 'owned' else {}))
            row[mode] = 'accepted'
        except jsonc.ConvertFail as error:
            failure = error
            row[mode] = error.message
        result = None
        row[mode + '_remaining'] = budget.snapshot()['used']['native_bytes']
    rows.append(row)
print(json.dumps(dict(python=sys.version, recursion_limit=sys.getrecursionlimit(), cases=rows), indent=2))
