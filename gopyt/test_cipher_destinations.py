import unittest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
from gopyt.security_config import _KeyringCipher


class CipherDestinations(unittest.TestCase):
    def test_real_keyring_fallback_matches_existing_format(self):
        old = AESGCMSIV(bytes(32))
        new = AESGCMSIV(bytes([1]) * 32)
        ring = _KeyringCipher({'old': old, 'new': new}, 'new', 'writer')
        nonce, data, aad = bytes(12), b'payload', b'context'
        encrypted = bytearray(len(data) + 16)
        self.assertEqual(ring.encrypt_into(nonce, data, aad, encrypted), len(encrypted))
        self.assertEqual(encrypted, ring.encrypt(nonce, data, aad))
        plain = bytearray(len(data))
        self.assertEqual(ring.decrypt_into(nonce, old.encrypt(nonce, data, aad), aad, plain), len(data))
        self.assertEqual(plain, data)

    def test_failed_reader_clears_partial_output_before_fallback(self):
        class Bad:
            def decrypt_into(self, nonce, data, aad, destination):
                destination[:] = b'x' * len(destination)
                raise InvalidTag
        class Good:
            def decrypt_into(inner, nonce, data, aad, destination):
                self.assertEqual(destination, bytes(len(destination)))
                destination[:] = b'valid'
                return 5
        ring = _KeyringCipher({'bad': Bad(), 'good': Good()}, 'bad', 'writer')
        result = bytearray(5)
        self.assertEqual(ring.decrypt_into(b'', b'', b'', result), 5)
        self.assertEqual(result, b'valid')

    def test_failure_and_cancellation_clear_large_retained_destination(self):
        for method in ('encrypt_into', 'decrypt_into'):
            for failure in (InvalidTag, KeyboardInterrupt):
                class Broken:
                    def write(self, nonce, data, aad, destination):
                        destination[:] = b'x' * len(destination)
                        raise failure()
                    encrypt_into = write
                    decrypt_into = write
                ring = _KeyringCipher({'key': Broken()}, 'key', 'writer')
                result = bytearray(9001)
                with self.subTest(method=method, failure=failure):
                    with self.assertRaises(failure):
                        getattr(ring, method)(b'', b'', b'', result)
                    self.assertEqual(result, bytes(len(result)))

    def test_plaintext_scope_charges_alias_and_clears_on_exit(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.security_config import seal, unseal_payload
        cipher = AESGCMSIV(bytes(32))
        setting = (_KeyringCipher({'key': cipher}, 'key', 'writer'), b'aad', b'id')
        encrypted = seal(b'secret', setting)
        budget = ResourceBudget(ResourceLimits(6, 0, 0, 0))
        with unseal_payload(encrypted, setting, budget) as plaintext:
            self.assertEqual(plaintext, b'secret')
            self.assertEqual(budget.snapshot()['used']['native_bytes'], 6)
        self.assertEqual(plaintext, bytes(6))
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 6)
        del plaintext
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_plaintext_rejection_does_not_invoke_cipher(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
        from gopyt.security_config import MAGIC, OVERHEAD, unseal_payload
        class Never:
            def decrypt_into(self, *args):
                raise AssertionError('cipher invoked before admission')
        budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        data = MAGIC + bytes(OVERHEAD - len(MAGIC) + 1)
        with self.assertRaises(ResourceLimitError):
            with unseal_payload(data, (Never(), b'', b''), budget):
                self.fail('plaintext published')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_ciphertext_scope_preserves_format_and_alias_charge(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.security_config import seal_payload, unseal, OVERHEAD
        cipher = AESGCMSIV(bytes(32))
        setting = (_KeyringCipher({'key': cipher}, 'key', 'writer'), b'aad', b'id')
        size = 6 + OVERHEAD
        budget = ResourceBudget(ResourceLimits(size + 12, 0, 0, 0))
        with seal_payload(b'secret', setting, budget) as encrypted:
            self.assertEqual(unseal(encrypted, setting), b'secret')
            self.assertEqual(budget.snapshot()['used']['native_bytes'], size)
            self.assertEqual(budget.snapshot()['peak']['native_bytes'], size + 12)
        self.assertEqual(encrypted, bytes(size))
        self.assertEqual(budget.snapshot()['used']['native_bytes'], size)
        del encrypted
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_failed_publication_clears_retained_ciphertext_without_losing_charge(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.security_config import OVERHEAD, unseal
        from gopyt.storage import Store
        class Context:
            resource_budget = ResourceBudget(ResourceLimits(1000, 0, 0, 0))
            def check_cancelled(self):
                pass
        class Database:
            def serialize(self):
                return b'secret'
        context = Context()
        store = Store(context=context)
        cipher = AESGCMSIV(bytes(32))
        store._security = (_KeyringCipher({'key': cipher}, 'key', 'writer'), b'aad', b'id')
        retained = []
        def publish(directory, data, **kwargs):
            self.assertEqual(unseal(data, store._security), b'secret')
            self.assertEqual(context.resource_budget.snapshot()['used']['native_bytes'], 6 + OVERHEAD)
            retained.append(data)
            raise OSError('publication failed')
        store._publish = publish
        with self.assertRaises(OSError):
            store._save(None, Database())
        self.assertEqual(retained[0], bytes(6 + OVERHEAD))
        self.assertEqual(context.resource_budget.snapshot()['used']['native_bytes'], 6 + OVERHEAD)
        retained.clear()
        self.assertEqual(context.resource_budget.snapshot()['active_reservations'], 0)
