import json
import sys

class Input(bytes):
    pass

results = []
for raw in (b'\xff', b'a' * 65536 + b'\xff', b'\xf0\x9f\x92'):
    source = Input(raw)
    try:
        source.decode('utf-8')
    except UnicodeDecodeError as error:
        results.append({'input_bytes': len(source), 'error_input_bytes': len(error.object),
                        'error_input_type': type(error.object).__name__,
                        'same_object': error.object is source,
                        'equal_content': error.object == source})
assert all(x['equal_content'] and not x['same_object'] for x in results)
print(json.dumps({'python': sys.version, 'cases': results,
                  'scope': 'finite strict UTF-8 error input-copy observations'}, indent=2))
