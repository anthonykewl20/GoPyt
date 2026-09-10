"""The release component and adaptation inventories stay complete and current."""
import json
from pathlib import Path
import unittest

from tools import adaptation_inventory, release_components

ROOT = Path(__file__).resolve().parent.parent


class ReleaseComponents(unittest.TestCase):
    def test_derived_inventory_matches_the_retained_one(self):
        self.assertEqual(release_components.check(ROOT), [])

    def test_every_pinned_input_is_inventoried(self):
        derived = release_components.derive(ROOT)['components']
        self.assertEqual(sorted(derived['python_distributions']),
                         ['cffi', 'cryptography', 'pip', 'pycparser', 'setuptools'])
        for name, action in derived['ci_actions'].items():
            self.assertRegex(action['commit'], r'^[0-9a-f]{40}$', name)
        for name, archive in derived['interpreter_archives'].items():
            self.assertRegex(archive['sha256'], r'^[0-9a-f]{64}$', name)

    def test_undeclared_licenses_stay_recorded_with_their_identity(self):
        derived = release_components.derive(ROOT)['components']
        known = dict(derived['installed_components'], **derived['interpreter_native_inputs'])
        # A missing publisher license is a recorded gap, not a pass: each entry
        # must still name the exact artifact a reviewer has to read.
        for entry in json.loads((ROOT / release_components.UNRESOLVED).read_text())['entries']:
            self.assertIn(entry['component'], known)
            self.assertRegex(entry['source_sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(entry['resolution_required'])

    def test_resolved_licenses_were_read_from_the_pinned_archive(self):
        derived = release_components.derive(ROOT)['components']
        known = dict(derived['installed_components'], **derived['interpreter_native_inputs'])
        resolved = json.loads((ROOT / release_components.RESOLVED).read_text())['entries']
        self.assertTrue(resolved)
        for entry in resolved:
            component = known[entry['component']]
            pinned = component.get('sha256') or component['hashes'].get('SHA-256')
            # A license is evidence only if it was read out of the archive this
            # repository already pinned, never inferred from the project name.
            self.assertEqual(entry['source_sha256'], pinned, entry['component'])
            self.assertTrue(entry['source_sha256_verified'])
            self.assertTrue(entry['license'])
            for record in entry['license_files']:
                self.assertRegex(record['sha256'], r'^[0-9a-f]{64}$')
                self.assertTrue(record['first_lines'])

    def test_an_undeclared_license_must_be_recorded_somewhere(self):
        derived = release_components.derive(ROOT)['components']
        recorded = set()
        for source in (release_components.RESOLVED, release_components.UNRESOLVED):
            recorded |= {entry['component'] for entry in
                         json.loads((ROOT / source).read_text())['entries']}
        for key, value in derived['installed_components'].items():
            if value['kind'] == 'native_component' and not value['licenses']:
                self.assertIn(key, recorded)
        for key, value in derived['interpreter_native_inputs'].items():
            if value['role'] == 'library' and not value['licenses']:
                self.assertIn(key, recorded)


class Adaptations(unittest.TestCase):
    def test_every_reference_is_classified(self):
        self.assertEqual(adaptation_inventory.check(ROOT), [])

    def test_adopted_code_carries_a_pinned_revision_and_license(self):
        entries = json.loads(
            (ROOT / adaptation_inventory.INVENTORY).read_text())['entries']
        adopted = [entry for entry in entries if entry.get('code_adopted')]
        self.assertTrue(adopted)
        for entry in adopted:
            self.assertTrue(entry.get('revision'), entry['source'])
            self.assertTrue(entry.get('license_record'), entry['source'])

    def test_unresolved_references_name_their_resolution(self):
        entries = json.loads(
            (ROOT / adaptation_inventory.INVENTORY).read_text())['entries']
        for entry in entries:
            if entry.get('unresolved'):
                self.assertTrue(entry.get('resolution_required'), entry['source'])

    def test_an_unclassified_reference_is_detected(self):
        found = adaptation_inventory.scan(ROOT)
        recorded = {entry['source'] for entry in json.loads(
            (ROOT / adaptation_inventory.INVENTORY).read_text())['entries']}
        self.assertEqual(set(found), recorded)
        self.assertNotIn('anthonykewl20/GoPyt', found)
