"""Compiled regressions for architecture audit R1–R3."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.values import UNIT
from gopyt.vm import VM, Cancelled


class NativeBoundaries(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'app'
        self.root.mkdir()
        head = 'module probe\n\nuse core.status { ModelError, NotFound, DbError }\n\n'
        signatures = {
            'local': 'task local() -> str | ModelError\n    effects { model }\n',
            'read': 'task read() -> str | NotFound | DbError\n    effects { database.read }\n',
            'pause': 'task pause(ms: i64) -> unit\n    effects { time }\n',
        }
        bodies = {'local': 'return core.model.local("probe", 1)',
                  'read': 'return store.db.get("item")',
                  'pause': 'return core.time.sleep_ms(ms)'}
        uses = ('use core.model { local }\nuse store.db { get }\n'
                'use core.time { sleep_ms }\n\n')
        write_pkg(str(self.root), {
            'spec/probe.gopyt': head + '\n'.join(signatures.values()),
            'impl/probe.gopyt': head + uses + '\n'.join(
                sig + '{\n    ' + bodies[name] + '\n}\n'
                for name, sig in signatures.items()),
        }, fmt=True)
        _, art, self.ids = build(str(self.root))
        self.vm = VM(art, str(self.root))

    def test_missing_optional_provider_returns_model_error(self):
        with patch.dict('sys.modules', {'peon': None, 'peon.local': None}):
            result = self.vm.call(self.ids['probe.local'], [])
        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.ModelError')

    def test_deep_policy_returns_db_error(self):
        policy = self.base / 'policy.json'
        policy.write_text('[' * 12000 + '0' + ']' * 12000)
        policy.chmod(0o600)
        with patch.dict(os.environ, {'GOPYT_DB_POLICY_FILE': str(policy)}):
            result = self.vm.call(self.ids['probe.read'], [])
        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.DbError')

    def test_policy_parser_recursion_returns_db_error(self):
        policy = self.base / 'policy.json'
        policy.write_text('{}')
        policy.chmod(0o600)
        with patch.dict(os.environ, {'GOPYT_DB_POLICY_FILE': str(policy)}), \
                patch('gopyt.capabilities.json.loads', side_effect=RecursionError):
            result = self.vm.call(self.ids['probe.read'], [])
        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.DbError')

    def test_maximum_sleep_can_be_cancelled_by_ancestor(self):
        outer, inner = threading.Event(), threading.Event()
        self.vm.cancels = (outer, inner)
        # Trigger after native entry, without depending on scheduler timing.
        def stop_after_wait(seconds):
            self.assertLessEqual(seconds, 0.05)
            outer.set()
        with patch('gopyt.natives.time.sleep', side_effect=stop_after_wait):
            with self.assertRaises(Cancelled):
                self.vm.call(self.ids['probe.pause'], [(1 << 63) - 1])
        self.assertEqual(self.vm.depth, 0)
        self.vm.cancels = ()
        self.assertIs(self.vm.call(self.ids['probe.pause'], [0]), UNIT)

    def test_sleep_uses_monotonic_elapsed_time(self):
        started = time.monotonic()
        self.assertIs(self.vm.call(self.ids['probe.pause'], [10]), UNIT)
        self.assertGreaterEqual(time.monotonic() - started, 0.01)

    def test_real_maximum_sleep_cancellation(self):
        cancel = threading.Event()
        self.vm.cancels = (cancel,)
        timer = threading.Timer(0.02, cancel.set)
        timer.start()
        self.addCleanup(timer.join)
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            self.vm.call(self.ids['probe.pause'], [(1 << 63) - 1])
        self.assertLess(time.monotonic() - started, 2)


if __name__ == '__main__':
    unittest.main()
