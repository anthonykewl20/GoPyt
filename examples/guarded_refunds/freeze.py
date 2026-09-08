"""Prepare a review bundle. Pin its printed hash in operator-owned configuration."""
import argparse
import json
from pathlib import Path
from gopyt.guard import canonical, digest, prepare_bundle
from examples.guarded_refunds.acceptance import cases, OBLIGATIONS

HERE = Path(__file__).resolve().parent


def bundle():
    result = json.loads(prepare_bundle(HERE/'policy', ['impl/refund_policy.gopyt'], cases(), OBLIGATIONS))
    result['adapter_sha256'] = digest((HERE/'service.py').read_bytes())
    result['acceptance_source_sha256'] = digest((HERE/'acceptance.py').read_bytes())
    return canonical(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    raw = bundle()
    with open(args.out, 'xb') as handle:
        handle.write(raw)
    print(digest(raw))
