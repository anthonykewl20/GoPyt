"""Verify a release against independently approved identities and revocations."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPOSITORY = 'anthonykewl20/GoPyt'
WORKFLOW = REPOSITORY + '/.github/workflows/release.yml'


def verify(artifact: Path, source: str, digest: str, revocations: Path, bundle=None):
    if not re.fullmatch(r'[0-9a-f]{40}', source):
        raise ValueError('expected a full lowercase source commit SHA')
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('expected a full lowercase artifact SHA256')
    policy = json.loads(revocations.read_text())
    if set(policy) != {'schema', 'artifacts', 'sources'} or policy['schema'] != 1:
        raise ValueError('unsupported revocation policy')
    for field, width in (('artifacts', 64), ('sources', 40)):
        if not isinstance(policy[field], dict) or any(
            not re.fullmatch('[0-9a-f]{' + str(width) + '}', key)
            or not isinstance(reason, str) or not reason.strip()
            for key, reason in policy[field].items()
        ):
            raise ValueError('malformed revocation entries')
    if source in policy['sources'] or digest in policy['artifacts']:
        raise ValueError('release identity has been revoked')
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError('artifact must be a regular non-symlink file')
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
        raise ValueError('artifact does not match approved SHA256')
    command = ['gh', 'attestation', 'verify', str(artifact.resolve()),
        '--repo', REPOSITORY, '--signer-workflow', WORKFLOW,
        '--signer-digest', source, '--source-digest', source,
        '--source-ref', 'refs/heads/main', '--deny-self-hosted-runners',
        '--predicate-type', 'https://slsa.dev/provenance/v1', '--format', 'json']
    if bundle is not None:
        command += ['--bundle', str(bundle.resolve())]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    verified = json.loads(result.stdout)
    if not isinstance(verified, list) or not verified:
        raise ValueError('no verified attestations')
    return {'source': source, 'artifact_sha256': digest, 'verification': verified}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--revocations', type=Path, required=True)
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.artifact, args.source_sha, args.sha256,
                            args.revocations, args.bundle), indent=2))


if __name__ == '__main__':
    main()
