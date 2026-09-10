"""Freeze strict JSON behavior before replacing allocation producers."""
import json
import sys
from decimal import Decimal
from gopyt.jsonc import parse, ConvertFail

valid = ['null', 'true', 'false', '0', '-0', '1.25', '1e1000000',
         '"\\ud83d\\ude00"', '"é中"', '[1,"x",null]', '{"a":[1],"b":{}}',
         ' \t\r\n{"x":1}\n']
invalid = ['{"x":1,"x":2}', '"\\ud800"', '"\\udc00"', 'NaN', 'Infinity',
           '01', '+1', '1.', '[1,]', '{"x":1,}', 'true false', '"\\x20"',
           '"\n"', '\ufeffnull']
results = []
for text in valid:
    expected = json.loads(text, parse_float=Decimal)
    actual = parse(text)
    assert actual == expected, text
    results.append({'input': text, 'accepted': True, 'type': type(actual).__name__})
for text in invalid:
    try:
        parse(text)
    except ConvertFail as error:
        results.append({'input': text, 'accepted': False, 'reason': str(error)})
    else:
        raise AssertionError(text)
print(json.dumps({'python': sys.version, 'cases': results,
                  'scope': 'finite pre-change behavior; valid values compared with standard JSON/Decimal'},
                 ensure_ascii=True, indent=2))
