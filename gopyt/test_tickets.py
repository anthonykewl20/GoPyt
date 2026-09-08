"""The on-disk ticket application must stay runnable and canonical."""
from pathlib import Path
import random
import shutil
import string
import tempfile
import unittest

from gopyt.testing import run_cli
from gopyt.cli import build, make_vm


class TicketExample(unittest.TestCase):
    def test_identifier_validation_matches_independent_character_and_length_oracle(self):
        """The frozen HTTP contract, independent of the application's algorithm."""
        allowed = string.ascii_letters + string.digits + '_-'
        cases = [chr(code) for code in range(256)]
        rng = random.Random(20260905)
        for _ in range(128):
            code = rng.randrange(0x110000)
            while 0xD800 <= code <= 0xDFFF:
                code = rng.randrange(0x110000)
            cases.append(chr(code))
        cases.extend('a' * size for size in range(67))
        cases.extend('A' + ch + '9' for ch in allowed)
        for character in ('.', '/', ' ', '\x00', '\n', 'é', '🙂', '\u0301'):
            for position in (0, 31, 63):
                cases.append('a' * position + character + 'a' * (63 - position))
        cases.append(allowed)
        example = Path(__file__).resolve().parents[1] / 'examples/tickets'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp, 'tickets')
            shutil.copytree(example, root, ignore=shutil.ignore_patterns(
                'build', 'evolve', '.gopyt-state', '.gopyt-transaction.lock'))
            prog, artifact, ids = build(str(root))
            vm = make_vm(str(root), prog, artifact, ids)
            for text in cases:
                with self.subTest(identifier=repr(text)):
                    expected = 1 <= len(text) <= 64 and all(ch in allowed for ch in text)
                    self.assertIs(vm.call(ids['tickets.valid_id'], [text]), expected)
                    vm.heap.release_result()

    def test_example_checks_tests_and_formats_without_source_changes(self):
        example = Path(__file__).resolve().parents[1] / 'examples/tickets'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp, 'tickets')
            shutil.copytree(example, root, ignore=shutil.ignore_patterns(
                'build', 'evolve', '.gopyt-state', '.gopyt-transaction.lock'))
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*.gopyt')}
            for command in ('fmt', 'check', 'test'):
                status, output = run_cli(str(root), command)
                self.assertEqual(status, 0, output)
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*.gopyt')}
            self.assertEqual(before, after)
