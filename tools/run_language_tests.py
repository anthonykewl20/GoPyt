"""Run unittest with periodic thread stacks to diagnose stalled CI suites."""
import faulthandler
import runpy
import sys
from pathlib import Path


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, repeat=True)
    try:
        runpy.run_module('unittest', run_name='__main__')
    finally:
        faulthandler.cancel_dump_traceback_later()
