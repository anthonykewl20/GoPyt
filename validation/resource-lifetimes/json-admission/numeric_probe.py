"""Observe numeric subclass ownership without asserting allocator bounds."""
import gc
import json
import sys
from decimal import Decimal

released = []
class Integer(int):
    def __new__(cls, text):
        value = super().__new__(cls, text)
        value.owner = 'integer'
        return value
    def __del__(self):
        released.append(self.owner)
class Number(Decimal):
    def __new__(cls, text):
        value = super().__new__(cls, text)
        value.owner = 'decimal'
        return value
    def __del__(self):
        released.append(self.owner)

rows = []
for cls, token in ((Integer, '123456789012345678901234567890'),
                   (Number, '1.25'), (Number, '1e1000000')):
    value = cls(token)
    alias = value
    expected = int(token) if cls is Integer else Decimal(token)
    assert value == expected and hash(value) == hash(expected)
    before = len(released)
    del value
    assert len(released) == before
    rows.append({'token': token, 'owner': alias.owner, 'object_size': sys.getsizeof(alias)})
    del alias
    gc.collect()
    assert len(released) == before + 1
print(json.dumps({'python': sys.version, 'cases': rows, 'finalized': released,
                  'scope': 'numeric subclass behavior; not allocation peak bounds'}, indent=2))
