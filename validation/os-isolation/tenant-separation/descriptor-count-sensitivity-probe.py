"""Is the global descriptor count perturbed by unrelated concurrent work?"""
import os, sys, threading, time
sys.path.insert(0, '/tmp/gopyt-mapping')
from unittest.mock import patch
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_buffer import Buffer

def budget():
    return ResourceBudget(ResourceLimits(16384, 8192, 2, 8))

stop = threading.Event()
def noise():
    # A daemon thread from an earlier test that is still finishing work.
    while not stop.is_set():
        fd = os.open('/dev/null', os.O_RDONLY)
        time.sleep(0.0001)
        os.close(fd)

def memfd_descriptors():
    found = []
    for name in os.listdir('/proc/self/fd'):
        try:
            target = os.readlink('/proc/self/fd/' + name)
        except OSError:
            continue
        if 'gopyt-buffer' in target:
            found.append((name, target))
    return found

worker = threading.Thread(target=noise, daemon=True)
worker.start()
count_mismatch = identity_mismatch = 0
trials = 400
for _ in range(trials):
    b = budget()
    before = len(os.listdir('/proc/self/fd'))
    with patch('gopyt.resource_mapping.os.memfd_create', side_effect=OSError('injected')):
        try:
            Buffer.map_bytes(b, b'abcd')
        except OSError:
            pass
    if len(os.listdir('/proc/self/fd')) != before:
        count_mismatch += 1
    if memfd_descriptors():
        identity_mismatch += 1
stop.set(); worker.join(2)
print(f'trials={trials} global-count mismatches={count_mismatch} '
      f'leaked gopyt-buffer descriptors={identity_mismatch}')
