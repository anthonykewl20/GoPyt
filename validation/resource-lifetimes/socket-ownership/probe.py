"""Observe real socket and TLS descriptor ownership; no network peer required."""
import json
import os
import platform
import socket
import ssl


def alive(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def observe(tls):
    left, right = socket.socketpair()
    fd = left.fileno()
    result = {'tls': tls, 'initial_live': alive(fd)}
    try:
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            wrapped = context.wrap_socket(left, server_hostname='localhost',
                                          do_handshake_on_connect=False)
            result['original_detached'] = left.fileno() == -1
            result['same_descriptor'] = wrapped.fileno() == fd
            left = wrapped
        reader = left.makefile('rb', buffering=0)
        try:
            left.close()
            result['live_after_socket_close'] = alive(fd)
        finally:
            reader.close()
        result['live_after_reader_close'] = alive(fd)
        assert result['initial_live'] and result['live_after_socket_close']
        assert not result['live_after_reader_close']
        if tls:
            assert result['original_detached'] and result['same_descriptor']
    finally:
        left.close()
        right.close()
    return result


print(json.dumps({'python': platform.python_version(),
                  'observations': [observe(False), observe(True)]}, indent=2))
