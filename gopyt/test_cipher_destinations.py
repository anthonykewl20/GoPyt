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
