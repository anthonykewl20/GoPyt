"""Frozen parser boundary probes, with literal expectations and wire outcomes.

Run from the repository root with PYTHONPATH=. on a pinned job interpreter.
This qualifies parser boundaries, not aggregate memory or sustained load.
"""
import hashlib
import http.client
import http.server
import io
import json
from pathlib import Path
import platform
import sys

from gopyt import natives, server, toolchain
from gopyt.test_app_runtime import running_server, wire
from gopyt.values import UNIT


def main():
    rows = []

    def headers(data):
        return http.client.parse_headers(io.BytesIO(data))

    def trailers(data):
        response = object.__new__(http.client.HTTPResponse)
        response.fp = io.BytesIO(data)
        response._read_and_discard_trailer()

    def case(name, operation, expected):
        try:
            operation()
            actual = 'accepted'
        except http.client.HTTPException as error:
            actual = type(error).__name__
        rows.append(dict(name=name, expected=expected, actual=actual))
        assert actual == expected, rows[-1]

    # Header parser counts the terminating blank line toward its 100 lines.
    case('99 headers plus terminator', lambda: headers(b'X: a\r\n' * 99 + b'\r\n'), 'accepted')
    case('100 headers plus terminator', lambda: headers(b'X: a\r\n' * 100 + b'\r\n'), 'HTTPException')
    case('65536 byte header line', lambda: headers(b'X:' + b'a' * 65532 + b'\r\n\r\n'), 'accepted')
    case('65537 byte header line', lambda: headers(b'X:' + b'a' * 65533 + b'\r\n\r\n'), 'LineTooLong')
    # Trailer parser counts nonterminating lines; no retained trailer list.
    case('100 trailers', lambda: trailers(b'X: a\r\n' * 100 + b'\r\n'), 'accepted')
    case('101 trailers', lambda: trailers(b'X: a\r\n' * 101 + b'\r\n'), 'HTTPException')
    case('65537 byte trailer line', lambda: trailers(b'X:' + b'a' * 65533 + b'\r\n\r\n'), 'LineTooLong')

    calls = []
    with running_server(MAX_HANDLERS=1, QUEUE=2) as (vm, port):
        vm.natives = dict(vm.natives)
        vm.natives['core.log.write'] = lambda *args: calls.append('handler') or UNIT
        trials = [
            ('request line over 65536', b'GET /' + b'a' * 65536 + b' HTTP/1.1\r\n\r\n', 414),
            ('header count overflow', b'GET /echo/a HTTP/1.1\r\n' + b'X: a\r\n' * 100 + b'\r\n', 431),
            ('header line overflow', b'GET /echo/a HTTP/1.1\r\nX:' + b'a' * 65533 + b'\r\n\r\n', 431),
            ('inbound transfer encoding rejected', b'GET /echo/a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n', 400),
            ('recovery after rejected requests', b'GET /echo/a HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n', 200),
        ]
        for name, data, expected in trials:
            status, body = wire(port, data)
            rows.append(dict(name=name, input_sha256=hashlib.sha256(data).hexdigest(),
                             input_bytes=len(data), expected=expected, actual=status,
                             body_bytes=len(body)))
            assert status == expected, rows[-1]
            assert len(calls) == int(expected == 200), calls
    sources = {}
    for module in (http.client, http.server, natives, server):
        path = Path(module.__file__)
        sources[module.__name__] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    print(json.dumps(dict(python=sys.version, platform=platform.platform(),
                         runtime_sha256=toolchain.FINGERPRINT.hex(), sources=sources,
                         probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                         trials=rows, handler_calls=len(calls),
                         scope='parser boundary and recovery probes; not aggregate memory or load qualification'), indent=2))


if __name__ == '__main__':
    main()
