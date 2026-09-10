"""What the isolation profile actually stops, and what it admits it does not."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from gopyt import isolation
from gopyt.guard import isolation_policy


def _requires_profile(case):
    usable, reason = isolation.available()
    if not usable:
        case.skipTest(reason)


def _run(case, script, **kwargs):
    _requires_profile(case)
    result, how = isolation.run([sys.executable, '-I', '-c', script],
                                timeout=60, **kwargs)
    return result, how


class Availability(unittest.TestCase):
    def test_description_states_what_is_not_provided(self):
        described = isolation.describe()
        self.assertEqual(described['platform'], sys.platform)
        self.assertEqual(described['supported'], sys.platform.startswith('linux'))
        self.assertIn('system-call filtering', described['not_provided'])
        self.assertIn('isolation of application code, which runs in the host process',
                      described['not_provided'])
        self.assertTrue(described['detail'])

    def test_an_unsupported_platform_reports_no_profile(self):
        with patch.object(isolation.sys, 'platform', 'darwin'):
            usable, reason = isolation.available()
            self.assertFalse(usable)
            self.assertIn('darwin', reason)
            self.assertFalse(isolation.supported())

    def test_entering_an_unsupported_platform_refuses(self):
        with patch.object(isolation.sys, 'platform', 'darwin'):
            with self.assertRaises(isolation.IsolationUnavailable):
                isolation.enter([], isolation.Limits())
            with self.assertRaises(isolation.IsolationUnavailable):
                isolation.run([sys.executable, '-c', 'pass'], required=True)


class HostileFixtures(unittest.TestCase):
    def test_the_network_is_unreachable(self):
        script = ('import json,socket\n'
                  'out={}\n'
                  'for name,target in (("dns",("1.1.1.1",53)),("http",("93.184.216.34",80))):\n'
                  '    try:\n'
                  '        socket.create_connection(target,timeout=2);out[name]="reached"\n'
                  '    except Exception as error: out[name]=type(error).__name__\n'
                  'try:\n'
                  '    s=socket.socket();s.bind(("0.0.0.0",0));out["bind"]=s.getsockname()[1]>0\n'
                  'except Exception as error: out["bind"]=type(error).__name__\n'
                  'print(json.dumps(out))')
        result, _how = _run(self, script)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertNotEqual(out['dns'], 'reached')
        self.assertNotEqual(out['http'], 'reached')
        # A loopback-only stack still exists; it reaches nothing outside itself.
        self.assertTrue(out['bind'])

    def test_the_host_filesystem_is_not_visible_or_writable(self):
        with tempfile.TemporaryDirectory(prefix='gopyt-secret-') as outside:
            secret = Path(outside, 'secret.txt')
            secret.write_text('operator key material')
            script = ('import json,os,sys\n'
                      'out={}\n'
                      'try:\n'
                      '    out["read"]=open(sys.argv[1]).read()\n'
                      'except Exception as error: out["read"]=type(error).__name__\n'
                      'for target in ("/usr/escape","/escape"):\n'
                      '    try:\n'
                      '        open(target,"w");out[target]="written"\n'
                      '    except Exception as error: out[target]=type(error).__name__\n'
                      'out["root"]=sorted(os.listdir("/"))\n'
                      'print(json.dumps(out))')
            _requires_profile(self)
            result, _how = isolation.run(
                [sys.executable, '-I', '-c', script, str(secret)], timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads(result.stdout)
            self.assertNotIn('operator key material', str(out['read']))
            self.assertEqual(out['/usr/escape'], 'OSError')
            self.assertIn(out['/escape'], ('OSError', 'PermissionError', 'FileNotFoundError'))
            self.assertNotIn('etc', out['root'])
            self.assertIn('work', out['root'])
            self.assertTrue(secret.exists())

    def test_only_the_named_work_directory_is_writable(self):
        with tempfile.TemporaryDirectory(prefix='gopyt-work-') as work:
            script = ('import json,os,sys\n'
                      'out={}\n'
                      'try:\n'
                      '    open(os.path.join(sys.argv[1],"produced.txt"),"w").write("ok")\n'
                      '    out["work"]="written"\n'
                      'except Exception as error: out["work"]=type(error).__name__\n'
                      'print(json.dumps(out))')
            _requires_profile(self)
            result, _how = isolation.run(
                [sys.executable, '-I', '-c', script, work], writable=[work], timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['work'], 'written')
            self.assertEqual(Path(work, 'produced.txt').read_text(), 'ok')

    def test_memory_exhaustion_is_bounded_not_fatal_to_the_host(self):
        script = ('import json\n'
                  'try:\n'
                  '    block=bytearray(1024*1024*1024)\n'
                  '    print(json.dumps({"allocated":len(block)}))\n'
                  'except MemoryError:\n'
                  '    print(json.dumps({"allocated":"MemoryError"}))')
        result, _how = _run(self, script, limits=isolation.Limits(
            address_space_bytes=128 * 1024 ** 2))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['allocated'], 'MemoryError')

    def test_process_creation_is_bounded(self):
        script = ('import json,os\n'
                  'started=0\n'
                  'try:\n'
                  '    while started < 512:\n'
                  '        pid=os.fork()\n'
                  '        if pid==0: os._exit(0)\n'
                  '        started+=1\n'
                  'except OSError as error: pass\n'
                  'print(json.dumps({"started":started}))')
        result, _how = _run(self, script, limits=isolation.Limits(processes=8))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(json.loads(result.stdout)['started'], 512)

    def test_descriptors_are_bounded(self):
        script = ('import json,os\n'
                  'opened=[]\n'
                  'try:\n'
                  '    while len(opened) < 4096:\n'
                  '        opened.append(os.open("/work", os.O_RDONLY))\n'
                  'except OSError: pass\n'
                  'print(json.dumps({"opened":len(opened)}))')
        result, _how = _run(self, script, limits=isolation.Limits(descriptors=64))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(json.loads(result.stdout)['opened'], 64)

    def _host_processes(self, marker):
        """Host processes whose command line carries `marker`.

        Namespace-local process ids mean nothing on the host, so the tree has to
        be counted from the host's own side.
        """
        found = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                command = (entry / 'cmdline').read_bytes()
            except OSError:
                continue
            if marker.encode() in command:
                found.append(int(entry.name))
        return found

    def test_a_runaway_child_tree_is_reaped_after_termination(self):
        _requires_profile(self)
        marker = f'gopyt-reap-{os.getpid()}-{time.monotonic_ns()}'
        script = ('import os,sys,time\n'
                  'for _ in range(3):\n'
                  '    if os.fork()==0:\n'
                  '        time.sleep(120)\n'
                  '        os._exit(0)\n'
                  'time.sleep(120)\n')
        with self.assertRaises(subprocess.TimeoutExpired):
            isolation.run([sys.executable, '-I', '-c', script, marker], timeout=3)
        # Signalling the session leader must take the whole tree with it; the
        # PID namespace dies with its first process.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self._host_processes(marker):
            time.sleep(.1)
        self.assertEqual(self._host_processes(marker), [])

    def test_the_tree_exists_before_it_is_reaped(self):
        _requires_profile(self)
        marker = f'gopyt-alive-{os.getpid()}-{time.monotonic_ns()}'
        script = 'import sys,time\ntime.sleep(120)\n'
        import threading
        outcome = []
        def run():
            try:
                isolation.run([sys.executable, '-I', '-c', script, marker], timeout=4)
            except subprocess.TimeoutExpired as error:
                outcome.append(error)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            deadline = time.monotonic() + 4
            seen = []
            while time.monotonic() < deadline and not seen:
                seen = self._host_processes(marker)
                time.sleep(.05)
            self.assertTrue(seen, 'the isolated process never appeared on the host')
        finally:
            worker.join(15)
        self.assertTrue(outcome)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self._host_processes(marker):
            time.sleep(.1)
        self.assertEqual(self._host_processes(marker), [])

    def test_no_new_privileges_is_set(self):
        script = ('import ctypes,json\n'
                  'libc=ctypes.CDLL(None,use_errno=True)\n'
                  'print(json.dumps({"no_new_privs":libc.prctl(39,0,0,0,0)}))')
        result, _how = _run(self, script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['no_new_privs'], 1)


class GuardPolicy(unittest.TestCase):
    def test_the_policy_values_are_closed(self):
        self.assertEqual(isolation_policy({}), 'preferred')
        for choice in ('required', 'preferred', 'off'):
            self.assertEqual(isolation_policy({'GOPYT_GUARD_ISOLATION': choice}), choice)
        with self.assertRaises(ValueError):
            isolation_policy({'GOPYT_GUARD_ISOLATION': 'maybe'})

    def test_required_isolation_refuses_when_it_cannot_be_installed(self):
        from gopyt import guard
        with patch.object(isolation, 'available', return_value=(False, 'kernel says no')), \
                patch.dict(os.environ, {'GOPYT_GUARD_ISOLATION': 'required'}):
            child, described = guard._isolated([sys.executable, '-c', 'pass'], '.', 5)
        self.assertIsNone(child)
        self.assertFalse(described['installed'])
        self.assertEqual(described['policy'], 'required')
        self.assertIn('kernel says no', described['detail'])

    def test_a_setup_failure_after_a_positive_probe_is_unavailability(self):
        # A probe can pass and the real setup still fail; that must not return
        # an unisolated result as though it were an isolated one.
        from gopyt import guard
        failure = isolation.IsolationUnavailable(
            'isolation failed: [Errno 13] Permission denied')
        with patch.object(isolation, 'available', return_value=(True, 'linux namespaces')), \
                patch.object(isolation, 'run', side_effect=failure), \
                patch.dict(os.environ, {'GOPYT_GUARD_ISOLATION': 'required'}):
            child, described = guard._isolated([sys.executable, '-c', 'pass'], '.', 5)
        self.assertIsNone(child)
        self.assertFalse(described['installed'])
        self.assertIn('Permission denied', described['detail'])
        with patch.object(isolation, 'available', return_value=(True, 'linux namespaces')), \
                patch.object(isolation, 'run', side_effect=failure), \
                patch.dict(os.environ, {'GOPYT_GUARD_ISOLATION': 'preferred'}):
            child, described = guard._isolated(
                [sys.executable, '-c', 'print(1)'], '.', 30)
        self.assertIsNotNone(child)
        self.assertEqual(child.stdout.strip(), '1')
        self.assertFalse(described['installed'])
        self.assertIn('Permission denied', described['detail'])

    def test_the_probe_exercises_the_whole_setup(self):
        # The probe runs the real entry path, so a host where only the first
        # syscall succeeds is reported unavailable rather than available.
        self.assertIn('from gopyt.isolation import Limits, enter', isolation._PROBE)
        self.assertIn('enter([], Limits())', isolation._PROBE)

    def test_a_receipt_never_implies_an_isolation_it_did_not_get(self):
        from gopyt import guard
        with patch.object(isolation, 'available', return_value=(False, 'kernel says no')), \
                patch.dict(os.environ, {'GOPYT_GUARD_ISOLATION': 'preferred'}):
            child, described = guard._isolated(
                [sys.executable, '-c', 'print(1)'], '.', 30)
        self.assertIsNotNone(child)
        self.assertFalse(described['installed'])
        self.assertEqual(described['policy'], 'preferred')
        with patch.dict(os.environ, {'GOPYT_GUARD_ISOLATION': 'off'}):
            child, described = guard._isolated(
                [sys.executable, '-c', 'print(1)'], '.', 30)
        self.assertFalse(described['installed'])
        self.assertIn('disabled by', described['detail'])
