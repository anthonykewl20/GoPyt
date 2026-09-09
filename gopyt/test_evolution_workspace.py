"""Proposal staging has an owner and is reclaimed after reaping/apply."""
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from gopyt import evolve
from gopyt.cli import cmd_fmt
from gopyt.manifest import package_digest
from gopyt.testing import write_lock
from gopyt.vm import Cancelled


class EvolutionWorkspace(unittest.TestCase):
    def test_real_waves_remove_candidates_and_preserve_legacy_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'auth'
            source = Path(__file__).resolve().parents[1]/'examples/auth'
            shutil.copytree(source, root, ignore=shutil.ignore_patterns('build', 'evolve'))
            legacy = root/'evolve/legacy/record'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'operator evidence')
            baseline = (root/'impl/auth.gopyt').read_text()
            for ttl in (3600000, 3600001):
                (root/'impl/auth.gopyt').write_text(baseline.replace('4, 3600000', f'4, {ttl}'))
                cmd_fmt(str(root))
                write_lock(str(root))
                result = evolve.propose(str(root), True, 2, timeout_ms=10000)
                self.assertEqual(result.kind, 'Applied', result)
                self.assertEqual(package_digest(str(root)), result.digest)
                self.assertEqual(list((root/'evolve').glob('.work-*')), [])
                self.assertEqual(legacy.read_bytes(), b'operator evidence')
                self.assertEqual(sorted(p.name for p in (root/'evolve').iterdir()), ['legacy','weights.json'])

    def test_prepare_error_and_cancellation_release_owned_files(self):
        with tempfile.TemporaryDirectory() as root:
            for failure in (OSError('prepare failed'), Cancelled()):
                def prepare(root, count, module, traces, workspace):
                    Path(workspace, 'partial').write_bytes(b'partial')
                    raise failure
                with self.subTest(failure=type(failure).__name__), patch('gopyt.evolve._prepare', prepare):
                    if isinstance(failure, Cancelled):
                        with self.assertRaises(Cancelled):
                            evolve.propose(root, True, 2)
                    else:
                        self.assertEqual(evolve.propose(root, True, 2).kind, 'EvolveError')
                    self.assertEqual(list(Path(root, 'evolve').glob('.work-*')), [])

    def test_concurrent_proposals_cannot_remove_each_others_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            ready = [threading.Event(), threading.Event()]
            release = [threading.Event(), threading.Event()]
            paths, outcomes = [], []
            def prepare(root, count, module, index, workspace):
                paths.append(Path(workspace))
                Path(workspace, 'candidate').write_text(str(index))
                ready[index].set()
                if not release[index].wait(5):
                    raise AssertionError('test release missing')
                return evolve.Outcome('NoChange')
            def run(index):
                try:
                    outcomes.append(evolve.propose(root, True, 2, traces=index))
                except BaseException as error:
                    outcomes.append(error)
            threads = [threading.Thread(target=run, args=(index,)) for index in (0,1)]
            with patch('gopyt.evolve._prepare', prepare):
                try:
                    threads[0].start()
                    self.assertTrue(ready[0].wait(2))
                    threads[1].start()
                    self.assertTrue(ready[1].wait(2))
                    self.assertNotEqual(paths[0], paths[1])
                    release[0].set()
                    threads[0].join(2)
                    self.assertFalse(threads[0].is_alive())
                    self.assertFalse(paths[0].exists())
                    self.assertEqual((paths[1]/'candidate').read_text(), '1')
                finally:
                    for event in release:
                        event.set()
                    for thread in threads:
                        if thread.ident is not None:
                            thread.join(5)
                self.assertEqual([result.kind for result in outcomes], ['NoChange','NoChange'])
                self.assertEqual(list(Path(root, 'evolve').glob('.work-*')), [])

    def test_cleanup_does_not_follow_links_or_delete_replaced_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            sentinel = Path(outside, 'keep')
            sentinel.write_text('outside')
            with evolve._proposal_workspace(root) as workspace:
                Path(workspace, 'link').symlink_to(outside, target_is_directory=True)
            self.assertEqual(sentinel.read_text(), 'outside')
            with self.assertRaises(OSError):
                with evolve._proposal_workspace(root) as workspace:
                    Path(workspace).rename(Path(root, 'moved'))
                    Path(workspace).symlink_to(outside, target_is_directory=True)
            self.assertEqual(sentinel.read_text(), 'outside')

    def test_cleanup_failure_preserves_primary_cancellation(self):
        with tempfile.TemporaryDirectory() as root:
            error = Cancelled()
            with patch('gopyt.evolve.shutil.rmtree', side_effect=OSError('cleanup denied')):
                with self.assertRaises(Cancelled) as caught:
                    with evolve._proposal_workspace(root):
                        raise error
            self.assertIs(caught.exception, error)
            self.assertIn('proposal cleanup failed: OSError', error.__notes__)

    def test_cleanup_failure_after_apply_does_not_undo_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'auth'
            shutil.copytree(Path(__file__).resolve().parents[1]/'examples/auth', root,
                            ignore=shutil.ignore_patterns('build', 'evolve'))
            write_lock(str(root))
            before = package_digest(str(root))
            original = shutil.rmtree
            def remove(path, *args, **kwargs):
                if str(path).startswith('.work-'):
                    raise OSError('cleanup denied')
                return original(path, *args, **kwargs)
            with patch('gopyt.evolve.shutil.rmtree', remove):
                result = evolve.propose(str(root), True, 2)
            self.assertEqual((result.kind, result.message), ('EvolveError','OSError'))
            self.assertNotEqual(package_digest(str(root)), before)
            self.assertTrue(list((root/'evolve').glob('.work-*')))
