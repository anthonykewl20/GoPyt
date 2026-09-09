"""Path dependency upgrades preserve review gates and deterministic rollback."""
from pathlib import Path
import shutil
import tempfile
import unittest

from gopyt.cli import build, cmd_fmt
from gopyt.diag import CompileError
from gopyt.gobyte import decode, encode
from gopyt.testing import write_pkg, write_lock
from gopyt.vm import VM, Trap


class DependencyUpgrades(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'app'
        self.prices = self.base / 'prices'
        self.rates = self.base / 'rates'
        for path in (self.root, self.prices, self.rates):
            path.mkdir()
        self.rate('7')
        write_pkg(str(self.prices), {
            'spec/prices.gopyt': 'module prices\n\nfn quote() -> i64\n',
            'impl/prices.gopyt': 'module prices\n\nuse rates { rate }\n\nfn quote() -> i64 {\n    return rates.rate() + 1\n}\n',
        }, name='prices', lock=False, fmt=True)
        self.dependency(self.prices, 'prices', 'rates')
        write_pkg(str(self.root), {
            'spec/demo.gopyt': 'module demo\n\nfn quote() -> i64\n',
            'impl/demo.gopyt': 'module demo\n\nuse prices { quote }\n\nfn quote() -> i64 {\n    return prices.quote()\n}\n',
        }, lock=False, fmt=True)
        self.dependency(self.root, 'test_package', 'prices')
        write_lock(str(self.root))
        _, art, self.ids = build(str(self.root))
        self.old = encode(art)
        self.old_lock = (self.root / 'gopyt.lock').read_bytes()
        self.saved_rates = {str(p.relative_to(self.rates)): p.read_bytes()
                            for p in self.rates.rglob('*') if p.is_file()
                            and not p.name.startswith('.gopyt')}
        self.assertEqual(self.result(art), 8)

    def dependency(self, root, name, dep):
        (root / 'gopyt.toml').write_text(
            f'name = "{name}"\nversion = "0.1.0"\n\n[deps]\n{dep} = {{ path = "../{dep}" }}\n')

    def rate(self, value, version='0.1.0', bound='0'):
        signature = f'fn rate() -> i64\n    ensures result >= {bound}\n'
        write_pkg(str(self.rates), {
            'spec/rates.gopyt': 'module rates\n\n' + signature,
            'impl/rates.gopyt': 'module rates\n\n' + signature + '{\n    return ' + value + '\n}\n',
        }, name='rates', version=version, lock=False, fmt=True)

    def result(self, art):
        vm = VM(art, str(self.root))
        return vm.call(vm.by_name['demo.quote'], [])

    def rejects_stale_without_writes(self):
        artifact_path = self.root / 'build' / 'out.gobyte'
        previous = artifact_path.read_bytes()
        with self.assertRaises(CompileError) as error:
            build(str(self.root))
        self.assertEqual(error.exception.diag.code, 41)
        self.assertEqual((self.root / 'gopyt.lock').read_bytes(), self.old_lock)
        self.assertEqual(artifact_path.read_bytes(), previous)
        # An explicitly retained old artifact remains the old compiled snapshot.
        self.assertEqual(self.result(decode(self.old)), 8)

    def test_transitive_upgrade_requires_review_and_rollback_is_reproducible(self):
        self.rate('9', version='0.2.0')
        self.rejects_stale_without_writes()
        write_lock(str(self.root))  # explicit operator acceptance in the fixture
        _, art, _ = build(str(self.root))
        self.assertEqual(self.result(art), 10)
        self.assertNotEqual(encode(art), self.old)
        lock = (self.root / 'gopyt.lock').read_text()
        self.assertIn('name = "rates"\nversion = "0.2.0"', lock)
        for name, data in self.saved_rates.items():
            (self.rates / name).write_bytes(data)
        (self.root / 'gopyt.lock').write_bytes(self.old_lock)
        _, restored, _ = build(str(self.root))
        self.assertEqual(encode(restored), self.old)
        self.assertEqual(self.result(restored), 8)

    def test_version_unchanged_does_not_hide_dependency_source_change(self):
        self.rate('11')
        self.rejects_stale_without_writes()
        write_lock(str(self.root))
        _, art, _ = build(str(self.root))
        self.assertEqual(self.result(art), 12)

    def test_contract_change_is_reviewed_and_enforced_after_rebuild(self):
        self.rate('7', bound='8')
        self.rejects_stale_without_writes()
        write_lock(str(self.root))
        _, art, _ = build(str(self.root))
        with self.assertRaises(Trap) as error:
            self.result(art)
        self.assertEqual(error.exception.code, 2)

    def test_reviewed_lock_cannot_make_incompatible_api_typecheck(self):
        signature = 'fn rate() -> str\n'
        (self.rates / 'spec/rates.gopyt').write_text('module rates\n\n' + signature)
        (self.rates / 'impl/rates.gopyt').write_text('module rates\n\n' + signature + '{\n    return "nine"\n}\n')
        cmd_fmt(str(self.rates))
        # Source semantic errors may precede lock validation; neither path
        # may rewrite the previous lock or artifact automatically.
        write_lock(str(self.root))
        accepted_lock = (self.root / 'gopyt.lock').read_bytes()
        with self.assertRaises(CompileError):
            build(str(self.root))
        self.assertEqual((self.root / 'gopyt.lock').read_bytes(), accepted_lock)
        self.assertEqual((self.root / 'build/out.gobyte').read_bytes(), self.old)

    def test_relocated_graph_compiles_to_identical_bytes_and_lock(self):
        clone = self.base / 'relocated'
        clone.mkdir()
        for name in ('app', 'prices', 'rates'):
            shutil.copytree(self.base / name, clone / name)
        _, art, _ = build(str(clone / 'app'))
        self.assertEqual(encode(art), self.old)
        self.assertEqual((clone / 'app/gopyt.lock').read_bytes(), self.old_lock)
