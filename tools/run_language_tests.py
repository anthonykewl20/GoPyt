"""Run unittest with periodic thread stacks to diagnose stalled CI suites."""
import faulthandler
import runpy
import sys
import threading
import traceback
from pathlib import Path


def report_stacks(stopped, interval=120, stream=None):
    stream = sys.__stderr__ if stream is None else stream
    while not stopped.wait(interval):
        print('Scheduled Python thread stacks:', file=stream, flush=True)
        frames = sys._current_frames()
        try:
            for identifier, frame in frames.items():
                print(f'Thread {identifier}:', file=stream)
                traceback.print_stack(frame, file=stream)
            stream.flush()
        finally:
            frames.clear()
            frame = None


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    faulthandler.enable()
    stopped = threading.Event()
    reporter = threading.Thread(target=report_stacks, args=(stopped,), daemon=True)
    reporter.start()
    try:
        runpy.run_module('unittest', run_name='__main__')
    finally:
        stopped.set()
        reporter.join(timeout=1)
