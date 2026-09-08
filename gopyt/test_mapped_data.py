import os
import unittest
from prototypes.mapped_data.sealed_column import available, sealed_column, validate, value_at, HEADER, MAGIC, VALUE


@unittest.skipUnless(available(), 'Linux memfd sealing prototype')
class MappedColumn(unittest.TestCase):
    def test_seals_block_resize_write_and_mapping_mutation(self):
        values = [-(2**63), -1, 0, 2**63 - 1]
        data = HEADER.pack(MAGIC, len(values)) + b''.join(VALUE.pack(v) for v in values)
        with sealed_column(data) as (fd, mapping):
            self.assertEqual([value_at(mapping, i) for i in range(4)], values)
            for size in (0, len(data) + 8):
                with self.assertRaises(OSError): os.ftruncate(fd, size)
            with self.assertRaises(OSError): os.pwrite(fd, b'bad', 0)
            with self.assertRaises(TypeError): mapping[0] = 0
            for index in (-1, 4, True, 1.5):
                with self.assertRaises(IndexError): value_at(mapping, index)

    def test_malformed_lengths_and_headers(self):
        for data in (b'', HEADER.pack(MAGIC, 2), HEADER.pack(b'BADMAGIC', 0), HEADER.pack(MAGIC, 0) + b'x'):
            with self.assertRaises(ValueError): validate(data)
