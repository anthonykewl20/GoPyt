"""Demonstrate contract drift and runtime checks on disposable ticket packages."""
from pathlib import Path
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gopyt.testing import run_cli, write_lock

EXAMPLE = Path(__file__).resolve().parent


def demonstrate():
    cases = [
        ('clean', None, None, 0, 0),
        ('deleted_postcondition', '    ensures result == version + 1\n', '', 1, None),
        ('weakened_precondition', '    requires version >= 1 and version < 1000000\n',
         '    requires version >= 0 and version < 1000000\n', 1, None),
        ('wrong_increment', '    return version + 1\n', '    return version + 2\n', 0, 2),
    ]
    rows = []
    with tempfile.TemporaryDirectory(prefix='gopyt-contract-demo-') as temp:
        for name, old, new, expected_check, expected_test in cases:
            package = Path(temp) / name
            shutil.copytree(EXAMPLE, package, ignore=shutil.ignore_patterns(
                'build', '.gopyt-state', '__pycache__', '.gopyt-transaction.lock'))
            if old is not None:
                implementation = package / 'impl/ticket_contracts.gopyt'
                source = implementation.read_text()
                if source.count(old) != 1:
                    raise ValueError('demo mutation anchor is not unique')
                implementation.write_text(source.replace(old, new))
                # Refresh the disposable package's digest so E041 cannot mask drift.
                write_lock(str(package))
            check_code, check_output = run_cli(str(package), 'check')
            test_code, test_output = (None, '') if check_code else run_cli(str(package), 'test')
            passed = check_code == expected_check and test_code == expected_test
            if name in ('deleted_postcondition', 'weakened_precondition'):
                passed = passed and 'GOPYT_E032' in check_output
            rows.append({'case': name, 'check_exit': check_code, 'check_output': check_output,
                         'test_exit': test_code, 'test_output': test_output, 'expected_behavior': passed})
    return {'schema': 'gopyt.contract-demo.v1', 'pass': all(r['expected_behavior'] for r in rows),
            'scope': 'Compiler drift protection and runtime-contract example, not a correctness proof.', 'cases': rows}


if __name__ == '__main__':
    result = demonstrate()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['pass'] else 1)
