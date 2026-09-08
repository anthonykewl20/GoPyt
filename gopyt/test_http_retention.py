"""Real HTTP connection teardown must not require cyclic garbage collection."""
import gc
import http.client
from http.server import BaseHTTPRequestHandler
import queue
import threading
import unittest
import weakref
from unittest.mock import patch

from gopyt.test_app_runtime import running_server


class HttpRetention(unittest.TestCase):
    def test_finished_handlers_are_reclaimed_with_cyclic_gc_disabled(self):
        finished = queue.Queue()
        original_finish = BaseHTTPRequestHandler.finish

        def observe_finish(handler):
            # Retain a weak reference only, after the real stream teardown.
            # This observes lifecycle behavior without inspecting the reader,
            # deadline representation, or other implementation internals.
            original_finish(handler)
            reclaimed = threading.Event()
            reference = weakref.ref(handler, lambda unused: reclaimed.set())
            finished.put((reference, reclaimed))

        with running_server(MAX_HANDLERS=2) as (_, port):
            enabled = gc.isenabled()
            gc.disable()
            try:
                references = []
                with patch.object(BaseHTTPRequestHandler, 'finish', observe_finish):
                    for _ in range(6):
                        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                        try:
                            connection.request('GET', '/echo/abc', headers={'Connection': 'close'})
                            response = connection.getresponse()
                            self.assertEqual((response.status, response.read()), (200, b'{"amount":3}'))
                        finally:
                            connection.close()
                        references.append(finished.get(timeout=2))
                    for reference, reclaimed in references:
                        self.assertTrue(reclaimed.wait(2), 'finished HTTP handler retained until cyclic GC')
                        self.assertIsNone(reference())
            finally:
                if enabled:
                    gc.enable()


if __name__ == '__main__':
    unittest.main()
