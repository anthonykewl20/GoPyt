"""Small exhaustive history oracle, independent of SQLite and Store internals."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from gopyt.storage import Store


def linearization(initial, history):
    """Return a legal ordering or None; exhaustively search up to eight calls.

    This intentionally small specification interpreter has no storage imports
    in its transition logic. A completed-before-invoked pair fixes real-time
    order. Overlapping calls may be serialized in either order.
    """
    if not 1 <= len(history) <= 8:
        raise ValueError('history oracle requires 1..8 completed calls')
    if any(h['start'] >= h['end'] for h in history):
        raise ValueError('invalid invocation/response interval')
    predecessors = [{j for j, other in enumerate(history) if other['end'] < h['start']}
                    for h in history]

    def search(state, order):
        if len(order) == len(history):
            return order
        done = set(order)
        for i, h in enumerate(history):
            if i in done or not predecessors[i] <= done:
                continue
            updated = dict(state)
            if h['kind'] == 'read':
                if [state.get(k) for k in h['args']] != h['result']:
                    continue
            elif h['kind'] == 'cas':
                accepted = all(state.get(k) == expected for k, expected, _ in h['args'])
                if accepted is not h['result']:
                    continue
                if accepted:
                    for k, _, value in h['args']:
                        if value is None:
                            updated.pop(k, None)
                        else:
                            updated[k] = value
            else:
                raise ValueError('unknown operation')
            found = search(updated, order + [i])
            if found is not None:
                return found
        return None

    return search(dict(initial), [])


def recorded(db, kind, args):
    start = time.monotonic_ns()
    result = db.get_many(args) if kind == 'read' else db.compare_exchange_many(args)
    return {'start': start, 'end': time.monotonic_ns(), 'kind': kind,
            'args': args, 'result': result}


def worker(root, request):
    db = Store(root)
    read = recorded(db, 'read', ['left', 'right', 'receipt'])
    left, right, receipt = read['result']
    # Duplicate delivery always requires an absent receipt, even if the read
    # already found it. No business transition may execute a second time.
    changes = [('left', left, str(int(left) - 1)),
               ('right', right, str(int(right) + 1)), ('receipt', None, request)]
    return [read, recorded(db, 'cas', changes)]


class HistoryOracle(unittest.TestCase):
    def call(self, kind, args, result, start=1, end=9):
        return dict(kind=kind, args=args, result=result, start=start, end=end)

    def test_accepts_overlap_and_respects_completed_before_invoked(self):
        write = self.call('cas', [('x', None, '1')], True, 1, 4)
        overlap = self.call('read', ['x'], [None], 2, 5)
        self.assertEqual(linearization({}, [write, overlap]), [1, 0])
        stale = self.call('read', ['x'], [None], 5, 6)
        self.assertIsNone(linearization({}, [write, stale]))

    def test_rejects_double_success_torn_read_and_false_conflict(self):
        write = self.call('cas', [('a', None, '1'), ('b', None, '1')], True)
        for history in ([write, dict(write)],
                        [write, self.call('read', ['a', 'b'], ['1', None])],
                        [self.call('cas', [('a', None, '1')], False)]):
            with self.subTest(history=history):
                self.assertIsNone(linearization({}, history))

    def test_absence_empty_deletion_and_read_conditions(self):
        history = [self.call('cas', [('a', '', None), ('b', 'same', 'same')], True, 1, 2),
                   self.call('read', ['a', 'b'], [None, 'same'], 3, 4)]
        self.assertEqual(linearization({'a': '', 'b': 'same'}, history), [0, 1])
        self.assertIsNone(linearization({'a': None, 'b': 'same'}, history))

    def test_bounded_input(self):
        with self.assertRaises(ValueError):
            linearization({}, [])
        with self.assertRaises(ValueError):
            linearization({}, [self.call('read', ['a'], [None])] * 9)
        with self.assertRaises(ValueError):
            linearization({}, [self.call('read', ['a'], [None], 2, 1)])


class ConcurrentHistories(unittest.TestCase):
    def exercise(self, processes):
        # Fixed workload, no retries or tuning to make a history pass. Each
        # history has three duplicated deliveries and one final observation.
        self.histories = []
        for trial in range(12):
            with self.subTest(trial=trial), tempfile.TemporaryDirectory() as temp:
                root = str(Path(temp).resolve())
                db = Store(root)
                initial = {'left': str(30 + trial), 'right': str(trial)}
                db.compare_exchange_many([(k, None, v) for k, v in initial.items()])
                def run(index):
                    if not processes:
                        return worker(root, 'request')
                    result = subprocess.run([sys.executable, '-m', __name__, root, 'request'],
                                            capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return json.loads(result.stdout)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    runs = list(pool.map(run, range(3)))
                history = [h for run in runs for h in run]
                history.append(recorded(Store(root), 'read', ['left', 'right', 'receipt']))
                witness = linearization(initial, history)
                self.assertIsNotNone(witness, json.dumps(history))
                self.histories.append(dict(initial=initial, history=history, witness=witness))
                self.assertEqual(sum(h['result'] is True for h in history if h['kind'] == 'cas'), 1)
                self.assertEqual(history[-1]['result'], [str(29 + trial), str(1 + trial), 'request'])
                # A fabricated second successful duplicate must be rejected by
                # the oracle for the very same measured invocation intervals.
                loser = next(h for h in history if h['kind'] == 'cas' and h['result'] is False)
                corrupted = [dict(h, result=True) if h is loser else h for h in history]
                self.assertIsNone(linearization(initial, corrupted), json.dumps(corrupted))

    def test_thread_histories(self):
        self.exercise(False)

    def test_process_histories(self):
        self.exercise(True)


class EncryptedHistories(ConcurrentHistories):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        key = Path(temp.name).resolve() / 'key'
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
        env = patch.dict(os.environ, {
            'GOPYT_SECURITY_PROFILE': 'strict',
            'GOPYT_STORE_KEY_FILE': str(key),
            'GOPYT_STORE_KEYRING_FILE': '',
            'GOPYT_STORE_ID': 'transaction-histories',
        })
        env.start()
        self.addCleanup(env.stop)


if __name__ == '__main__':
    print(json.dumps(worker(*sys.argv[1:])))
