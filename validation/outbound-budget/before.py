import hashlib
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
from gopyt.test_resource_authority import NetworkCalls
from gopyt.toolchain import FINGERPRINT, source_fingerprint
from gopyt.vm import Trap

report = {'runtime_sha256': FINGERPRINT.hex(),
          'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'fixture_sha256': hashlib.sha256(Path('gopyt/test_resource_authority.py').read_bytes()).hexdigest(),
          'deadline_ms': 100, 'body_delay_ms': 300, 'cases': []}
fixture = NetworkCalls()
fixture.setUp()
try:
    for mode in ('http', 'model'):
        body_ready = threading.Event()
        def delayed(handler):
            body = b'{"text":"delayed"}'
            handler.send_response(200)
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            time.sleep(.3)
            body_ready.set()
            handler.wfile.write(body)
        with patch.object(fixture.server.RequestHandlerClass, 'do_GET', delayed), \
                patch.dict(os.environ, {'GOPYT_MODEL_URL': fixture.origin + '/delayed'}):
            start = time.monotonic_ns()
            fixture.vm.deadline_ns = start + 100_000_000
            try:
                if mode == 'http':
                    fixture.request(fixture.origin + '/delayed')
                else:
                    fixture.vm.call(fixture.ids['remote.complete'], [])
            except Trap as error:
                outcome = {'trap': error.code}
            else:
                outcome = {'return': 'success'}
            finally:
                fixture.vm.deadline_ns = None
            report['cases'].append({'mode': mode, 'outcome': outcome,
                                    'elapsed_ms': (time.monotonic_ns() - start) / 1_000_000,
                                    'body_ready_before_return': body_ready.is_set()})
finally:
    fixture.doCleanups()
assert source_fingerprint(Path('gopyt')) == FINGERPRINT
Path('/tmp/gopyt-client-deadline-before.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
