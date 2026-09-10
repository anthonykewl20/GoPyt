import gc
import faulthandler
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_bytes import ByteBuilder
faulthandler.dump_traceback_later(1)
gc.disable()
budget = ResourceBudget(ResourceLimits(100, 0, 0, 0))
builder = ByteBuilder(budget)
builder.append_text('abc')
payload = builder.finish()
cycle = [payload.data]
cycle.append(cycle)
payload.close()
del cycle
with budget._lock:
    gc.collect()
print('completed')
