import gc
import os
from pathlib import Path
import subprocess
import sys
import unittest

from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_bytes import ByteBuilder


class BudgetFinalizers(unittest.TestCase):
    def test_collection_under_lock_completes_in_bounded_process(self):
        root = Path(__file__).resolve().parents[1]
        probe = root / 'validation/resource-lifetimes/budget-reentrancy/probe.py'
        result = subprocess.run([sys.executable, str(probe)], cwd=root,
                                env=dict(os.environ, PYTHONPATH=str(root)),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'completed')

    def test_collection_during_reserve_preserves_counter_update(self):
        enabled = gc.isenabled()
        gc.disable()
        try:
            budget = ResourceBudget(ResourceLimits(100, 0, 0, 0))
            builder = ByteBuilder(budget)
            builder.append_text('abc')
            payload = builder.finish()
            cycle = [payload.data]
            cycle.append(cycle)
            payload.close()
            del cycle
            class CollectingCounters(dict):
                collected = False
                def __getitem__(self, key):
                    if not self.collected:
                        self.collected = True
                        gc.collect()
                    return super().__getitem__(key)
            budget._used = CollectingCounters(budget._used)
            reservation = budget.reserve(native_bytes=5)
            self.assertEqual(budget.snapshot()['used']['native_bytes'], 5)
            self.assertEqual(budget.snapshot()['active_reservations'], 1)
            reservation.release()
            self.assertEqual(budget.snapshot()['used']['native_bytes'], 0)
        finally:
            if enabled:
                gc.enable()
