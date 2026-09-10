import inspect,json,sys
import cryptography
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
cipher=AESGCMSIV(bytes(32));nonce=bytes(12);data=b'payload';aad=b'context'
output=bytearray(len(data)+16)
written=cipher.encrypt_into(nonce,data,aad,output)
assert written==len(output) and bytes(output)==cipher.encrypt(nonce,data,aad)
plain=bytearray(len(data))
read=cipher.decrypt_into(nonce,memoryview(output),aad,plain)
assert read==len(data) and plain==data
print(json.dumps({'python':sys.version,'cryptography':cryptography.__version__,'cipher':'AESGCMSIV','encrypt_into_bytes':written,'decrypt_into_bytes':read,'matches_allocating_api':True,'scope':'buffer API capability and one roundtrip; no native allocator bound'}))
