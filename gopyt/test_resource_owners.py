"""Actual command ownership closes native resources on success and trap."""
import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

from gopyt import cli
from gopyt.testing import write_pkg
from gopyt.test_vm import module


class ResourceOwners(unittest.TestCase):
    def test_run_owner_closes_on_success_and_trap(self):
        for fail in (False,True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as root:
                signature='task main() -> unit\n    effects { resource }'
                body=signature+'\n{\n    value = data.buffer.allocate(8)\n'
                if fail:body+='    core.test.assert_eq(1, 2)\n'
                body+='    return unit\n}\n'
                uses='use data.buffer { allocate }'
                if fail:uses='use core.test { assert_eq }\n'+uses
                write_pkg(root,module(signature,body,uses=uses),fmt=True)
                made=[];original=cli.make_vm
                def make(*args):
                    vm=original(*args);made.append(vm);return vm
                with patch.object(cli,'make_vm',make), contextlib.redirect_stdout(io.StringIO()):
                    code=cli.cmd_run(root,'demo.main')
                self.assertEqual(code,cli.EXIT_TRAP if fail else cli.EXIT_OK)
                self.assertEqual(len(made),1)
                self.assertTrue(made[0]._closed)
                self.assertEqual(made[0].resource_budget.snapshot()['peak']['native_bytes'],8)
                self.assertEqual(made[0].resource_budget.snapshot()['active_reservations'],0)
