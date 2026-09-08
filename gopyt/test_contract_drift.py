"""Spec/impl contracts must match in content and order, not merely count."""
import tempfile
import unittest

from gopyt.testing import diag_code, write_pkg


class ContractDriftTests(unittest.TestCase):
    def check_twins(self, spec_contracts, impl_contracts):
        with tempfile.TemporaryDirectory() as root:
            write_pkg(root, {
                'spec/demo.gopyt': 'module demo\n\nfn bump(value: i64) -> i64\n' + spec_contracts,
                'impl/demo.gopyt': 'module demo\n\nfn bump(value: i64) -> i64\n' + impl_contracts + '{\n    return value + 1\n}\n',
            }, fmt=True)
            return diag_code(root)

    def test_changed_literal_is_drift(self):
        self.assertEqual(self.check_twins('    requires value >= 1\n', '    requires value >= 0\n'), 32)

    def test_changed_operator_is_drift(self):
        self.assertEqual(self.check_twins('    ensures result == value + 1\n',
                                         '    ensures result >= value + 1\n'), 32)

    def test_changed_contract_kind_is_drift(self):
        self.assertEqual(self.check_twins('    requires value >= 1\n', '    ensures value >= 1\n'), 32)

    def test_reordered_clauses_are_drift(self):
        self.assertEqual(self.check_twins('    requires value >= 1\n    requires value < 100\n',
                                         '    requires value < 100\n    requires value >= 1\n'), 32)

    def test_nested_expression_change_is_drift(self):
        self.assertEqual(self.check_twins('    ensures result == (value + 1) * 2\n',
                                         '    ensures result == (value + 2) * 2\n'), 32)

    def test_comments_and_source_lines_are_not_drift(self):
        self.assertEqual(self.check_twins('    requires value >= 1\n',
                                         '    // implementation note\n    requires value >= 1\n'), 0)


if __name__ == '__main__':
    unittest.main()
