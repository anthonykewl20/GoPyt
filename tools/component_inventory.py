"""Read the installed release inputs, including publisher-supplied bundled metadata."""
import base64
import email.parser
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys


def inventory(name):
    dist = importlib.metadata.distribution(name)
    records = []
    nested = []
    vendor_pins = []
    sboms = []
    hashed = 0
    unhashed = []
    for item in dist.files or []:
        path = dist.locate_file(item)
        if not path.is_file():
            raise ValueError('missing installed distribution file: ' + str(item))
        data = path.read_bytes()
        if item.hash:
            actual = base64.urlsafe_b64encode(hashlib.new(item.hash.mode, data).digest()).decode().rstrip('=')
            if actual != item.hash.value or (item.size is not None and len(data) != item.size):
                raise ValueError('installed bytes differ from RECORD: ' + str(item))
            hashed += 1
        else:
            if not str(item).endswith(('.pyc', '.dist-info/RECORD')):
                raise ValueError('unexpected unhashed installed file: ' + str(item))
            unhashed.append(str(item))
        lowered = str(item).lower()
        if str(item).endswith('/METADATA') and '/_vendor/' in str(item):
            metadata = email.parser.BytesParser().parsebytes(data)
            nested.append({'name': metadata['Name'], 'version': metadata['Version'],
                'license_expression': metadata['License-Expression'],
                'license': metadata['License'], 'requires_dist': metadata.get_all('Requires-Dist', []),
                'metadata_path': str(item)})
        if str(item).endswith('/vendor.txt'):
            for line in data.decode().splitlines():
                match = re.fullmatch(r'\s*([A-Za-z0-9_.-]+)==([^\s#]+)\s*', line)
                if match:
                    vendor_pins.append({'name': match[1], 'version': match[2], 'evidence': str(item)})
                elif line.strip() and not line.lstrip().startswith('#'):
                    raise ValueError('unrecognized vendor declaration: ' + line)
        if '/sboms/' in lowered and lowered.endswith('.json'):
            sboms.append({'path': str(item), 'document': json.loads(data)})
        if ('license' in path.name.lower() or str(item).endswith('/vendor.txt')
                or ('/sboms/' in lowered and lowered.endswith('.json'))):
            records.append({'path': str(item), 'sha256': hashlib.sha256(data).hexdigest(),
                'text': data.decode()})
    return {'name': dist.metadata['Name'], 'version': dist.version,
        'license_expression': dist.metadata['License-Expression'],
        'requires_dist': dist.metadata.get_all('Requires-Dist', []),
        'verified_record_files': hashed, 'unhashed_installer_files': unhashed,
        'bundled_metadata': nested, 'vendor_pins': vendor_pins, 'sboms': sboms,
        'license_and_inventory_records': records}


if __name__ == '__main__':
    print(json.dumps({'python': sys.version, 'platform': platform.platform(),
        'distributions': [inventory(name) for name in ('pip','setuptools','cryptography','cffi','pycparser')],
        'limits': ['Installed environment evidence, not publisher authentication.',
            'Publisher-supplied bundled metadata is preserved, not independently audited.',
            'Other-platform binaries, CI actions, interpreter-native components and repository adaptations need separate coverage.',
            'Requires-Dist extras are upstream metadata; inclusion does not adopt those dependencies.']},indent=2))
