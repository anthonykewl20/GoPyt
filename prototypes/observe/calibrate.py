"""Report the exact planted-shift and control experiment in validation.md P0."""

import json
import random

from sketches import Observe


def calibrate(trials=20):
    control_alarms = 0
    shift_alarms = 0
    delays = []
    cells = set()
    for seed in range(trials):
        control = Observe(reservoir_k=32)
        shifted = Observe(reservoir_k=32)
        stream = random.Random(100 + seed)
        cells.add(shifted.memory_cells())
        first = None
        for index in range(3400):
            value = stream.random()
            control.event("control", value < 0.01, 10.0)
            shifted.event("shift", value < (0.01 if index < 3000 else 0.15), 10.0)
            if index >= 3000 and shifted.cusum.alarm and first is None:
                first = index - 3000 + 1
        control_alarms += control.cusum.alarm
        shift_alarms += shifted.cusum.alarm
        if first is not None:
            delays.append(first)
        cells.add(shifted.memory_cells())
        assert len(shifted.reservoir.items) <= 32
    return {"trials": trials, "events_per_trial": 3400, "shift_at": 3000,
            "baseline_probability": 0.01, "shift_probability": 0.15,
            "control_alarms": control_alarms, "shift_alarms": shift_alarms,
            "post_shift_detection_events": delays, "memory_cells": sorted(cells)}


if __name__ == "__main__":
    result = calibrate()
    print(json.dumps(result, sort_keys=True, indent=2))
    raise SystemExit(0 if result["control_alarms"] < 5 and result["shift_alarms"] == result["trials"]
                     and len(result["memory_cells"]) == 1 else 1)
