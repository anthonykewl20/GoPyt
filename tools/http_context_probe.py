"""Replay an idle listener's inherited deadline with retained source identity."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gopyt.cli import build, make_vm
from gopyt.testing import write_pkg
from gopyt.test_vm import API_FILES
from gopyt.toolchain import FINGERPRINT, source_fingerprint
from gopyt.vm import Trap


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'runtime_sha256': FINGERPRINT.hex(), 'probe_sha256': digest,
              'fixture_sha256': hashlib.sha256(json.dumps(API_FILES, sort_keys=True).encode()).hexdigest(),
              'deadline_ms': 300, 'manual_shutdown_after_ms': 600}
    with args.output.open('x') as output:
        with tempfile.TemporaryDirectory() as root, \
                patch('gopyt.server._addr', return_value=('127.0.0.1', 0)), \
                patch('gopyt.server.MAX_HANDLERS', 2):
            write_pkg(root, API_FILES)
            prog, art, ids = build(root)
            vm = make_vm(root, prog, art, ids)
            outcome = []

            def run():
                start = time.monotonic_ns()
                vm.deadline_ns = start + 300_000_000
                try:
                    vm.call(ids['api.serve'], [])
                    outcome.append({'return': 'success'})
                except Trap as error:
                    outcome.append({'trap': error.code})
                finally:
                    report['elapsed_ms'] = (time.monotonic_ns() - start) / 1_000_000

            thread = threading.Thread(target=run)
            thread.start()
            try:
                time.sleep(.6)
                report['still_running_after_deadline'] = thread.is_alive()
            finally:
                if getattr(vm, 'httpd', None) is not None:
                    vm.httpd.shutdown()
                thread.join(5)
            report['joined_after_manual_shutdown'] = not thread.is_alive()
            report['outcome'] = outcome
            report['serving_after_cleanup'] = vm.serving
        assert source_fingerprint(ROOT / 'gopyt') == FINGERPRINT
        assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == digest
        output.write(json.dumps(report, indent=2) + '\n')
    assert not report['still_running_after_deadline']
    assert report['joined_after_manual_shutdown'] and not report['serving_after_cleanup']
    assert report['outcome'] == [{'trap': 6}]
    print(json.dumps(report))


if __name__ == '__main__':
    main()
