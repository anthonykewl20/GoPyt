"""Compiler-level opaque resource and effect admission rules."""
import tempfile
import unittest
from gopyt.testing import write_pkg, diag_code


class ResourceTypes(unittest.TestCase):
    def check_source(self, signature, body):
        with tempfile.TemporaryDirectory() as root:
            uses=[]
            if 'ResourceError' in signature:
                uses.append('use core.status { ResourceError }')
            names=['Buffer']
            if 'allocate(' in body:
                names.append('allocate')
            uses.append('use data.buffer { '+', '.join(names)+' }')
            if 'data.json.encode' in body:
                uses.append('use data.json { encode }')
            header='module demo\n\n'+'\n'.join(uses)+'\n\n'
            spec_header=header.replace('{ Buffer, allocate }','{ Buffer }').replace('use data.json { encode }\n','')
            write_pkg(root, {'spec/demo.gopyt':spec_header+signature+'\n',
                'impl/demo.gopyt':header+signature+'\n{\n    '+body+'\n}\n'})
            return diag_code(root)

    def test_resource_effect_and_opaque_return_are_admitted(self):
        self.assertEqual(self.check_source('task make() -> Buffer | ResourceError\n    effects { resource }',
            'return data.buffer.allocate(4)'),0)

    def test_resource_call_requires_its_declared_effect(self):
        self.assertNotEqual(self.check_source('fn make() -> Buffer | ResourceError',
            'return data.buffer.allocate(4)'),0)

    def test_opaque_resource_equality_is_rejected(self):
        self.assertNotEqual(self.check_source('fn same(left: Buffer, right: Buffer) -> bool',
            'return left == right'),0)

    def test_opaque_resource_json_is_rejected(self):
        self.assertEqual(self.check_source('fn encode(value: list[Buffer]) -> str',
            'return data.json.encode(value)'),112)
