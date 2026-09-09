import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.verify_release import verify


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.artifact = self.root / 'candidate.whl'
        self.artifact.write_bytes(b'candidate')
        self.digest = hashlib.sha256(b'candidate').hexdigest()
        self.source = 'a' * 40
        self.policy = self.root / 'revocations.json'
        self.policy.write_text(json.dumps({'schema': 1, 'sources': {}, 'artifacts': {}}))

    def run_verify(self):
        return verify(self.artifact, self.source, self.digest, self.policy)

    @patch('tools.verify_release.subprocess.run')
    def test_hash_mismatch_rejected_before_attestation(self, run):
        self.artifact.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.run_verify()
        run.assert_not_called()

    @patch('tools.verify_release.subprocess.run')
    def test_revoked_source_or_artifact_rejected(self, run):
        for field, identity in (('sources', self.source), ('artifacts', self.digest)):
            policy = {'schema': 1, 'sources': {}, 'artifacts': {}}
            policy[field][identity] = 'withdrawn after vulnerability report'
            self.policy.write_text(json.dumps(policy))
            with self.assertRaisesRegex(ValueError, 'revoked'):
                self.run_verify()
        run.assert_not_called()

    @patch('tools.verify_release.subprocess.run')
    def test_missing_or_malformed_policy_fails_closed(self, run):
        for policy in ({}, {'schema': 2, 'sources': {}, 'artifacts': {}},
                       {'schema': 1, 'sources': {self.source: ''}, 'artifacts': {}}):
            self.policy.write_text(json.dumps(policy))
            with self.assertRaises(ValueError):
                self.run_verify()
        self.policy.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_verify()
        run.assert_not_called()

    @patch('tools.verify_release.subprocess.run')
    def test_signature_failure_cannot_be_accepted(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ['gh'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_verify()
        run.side_effect = None
        run.return_value.stdout = '[]'
        with self.assertRaisesRegex(ValueError, 'no verified'):
            self.run_verify()

    @patch('tools.verify_release.subprocess.run')
    def test_verifier_constrains_signed_identity(self, run):
        run.return_value.stdout = '[{"verified": true}]'
        result = self.run_verify()
        command = run.call_args.args[0]
        for flag, value in (
            ('--repo', 'anthonykewl20/GoPyt'),
            ('--signer-workflow', 'anthonykewl20/GoPyt/.github/workflows/release.yml'),
            ('--source-digest', self.source), ('--signer-digest', self.source),
            ('--source-ref', 'refs/heads/main'),
            ('--predicate-type', 'https://slsa.dev/provenance/v1'),
        ):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn('--deny-self-hosted-runners', command)
        self.assertTrue(run.call_args.kwargs['check'])
        self.assertEqual(result['artifact_sha256'], self.digest)

    @patch('tools.verify_release.subprocess.run')
    def test_abbreviated_source_and_symlink_rejected(self, run):
        self.source = 'abc'
        with self.assertRaises(ValueError):
            self.run_verify()
        self.source = 'a' * 40
        target = self.root / 'actual.whl'
        self.artifact.rename(target)
        self.artifact.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'non-symlink'):
            self.run_verify()
        run.assert_not_called()
