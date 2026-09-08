"""Additional non-score checks of deadline implementation and preserved execution."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from gopyt.guard import compile_policy,timed_call
from gopyt.testing import write_pkg
from gopyt.vm import Cancelled

class DeadlineExecution(unittest.TestCase):
    def test_actual_vm_loop_stops_and_next_call_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig='fn count(limit: i64) -> i64\n'
            write_pkg(tmp,{'spec/loops.gopyt':'module loops\n\n'+sig,
              'impl/loops.gopyt':'module loops\n\nuse core.list { range }\n\n'+sig+'{\n    for value in core.list.range(0, limit) {\n        if value < 0 {\n            return value\n        }\n    }\n    return limit\n}\n'},name='loops',fmt=True)
            vm,ids=compile_policy(Path(tmp));start=time.monotonic()
            with self.assertRaises(Cancelled):timed_call(vm,ids['loops.count'],[10000],timeout=0.001)
            self.assertLess(time.monotonic()-start,1)
            self.assertEqual(vm.cancels,())
            self.assertEqual(timed_call(vm,ids['loops.count'],[4]),4)

    def test_no_timer_thread_is_allocated(self):
        class VM:
            cancels=()
            def call(self,fn,args):return args[0]
        with patch('threading.Timer',side_effect=AssertionError('timer allocated')):
            self.assertEqual(timed_call(VM(),0,[9]),9)
