"""Deterministic typed-program generation against an independent integer oracle."""
from pathlib import Path
import random
import tempfile
import unittest
from gopyt.cli import build, make_vm
from gopyt.testing import write_pkg
from gopyt.vm import Trap


class GeneratedPrograms(unittest.TestCase):
    def test_arithmetic_branches_and_overflow(self):
        rng = random.Random(4701)
        specs = ['module generated\n']
        impls = ['module generated\n']
        cases = []
        for index in range(48):
            multiplier = rng.randrange(1, 20)
            offset = rng.randrange(0, 100)
            threshold = rng.randrange(-100, 101)
            name = f'calculate_{index}'
            signature = f'fn {name}(value: i64) -> i64\n'
            specs.append(signature)
            impls.append(signature + '{\n' + f'    if value < {threshold} {{\n        return value * {multiplier} + {offset}\n    }}\n    return value - {offset}\n}}\n')
            cases.append((name, multiplier, offset, threshold))
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {'spec/generated.gopyt': '\n'.join(specs), 'impl/generated.gopyt': '\n'.join(impls)})
            program, artifact, ids = build(root)
            vm = make_vm(root, program, artifact, ids)
            for name, multiplier, offset, threshold in cases:
                for value in [-2**63, -2**63+1, -101, -1, 0, 1, 101, 2**63-1, threshold-1, threshold, threshold+1]:
                    intermediates = [value*multiplier, value*multiplier+offset] if value < threshold else [value-offset]
                    overflow = any(not -2**63 <= result < 2**63 for result in intermediates)
                    with self.subTest(name=name, value=value):
                        if overflow:
                            with self.assertRaises(Trap): vm.call(ids['generated.'+name], [value])
                        else:
                            self.assertEqual(vm.call(ids['generated.'+name], [value]), intermediates[-1])
