"""P0 pass criteria from docs/validation.md."""

from __future__ import annotations

import random
import unittest

from sketches import CountMin, Observe, Reservoir, Welford


class TestWelford(unittest.TestCase):
    def test_mean_var(self) -> None:
        w = Welford()
        xs = [1.0, 2.0, 3.0, 4.0]
        for x in xs:
            w.add(x)
        self.assertAlmostEqual(w.mean, 2.5)
        self.assertAlmostEqual(w.variance(), 5.0 / 3.0)


class TestReservoir(unittest.TestCase):
    def test_cap(self) -> None:
        random.seed(1)
        r = Reservoir(8)
        for i in range(10000):
            r.add(str(i))
        self.assertEqual(len(r.items), 8)
        self.assertEqual(r.seen, 10000)


class TestCountMin(unittest.TestCase):
    def test_never_under_true_count(self) -> None:
        cms = CountMin(depth=4, width=256)
        for _ in range(50):
            cms.add("denied")
        self.assertGreaterEqual(cms.estimate("denied"), 50)


class TestCusumObserve(unittest.TestCase):
    def test_shift_alarms(self) -> None:
        rng = random.Random(2)
        obs = Observe(reservoir_k=32)
        for _ in range(3000):
            fail = rng.random() < 0.01
            obs.event("denied" if fail else "ok", fail, 10.0)
        for _ in range(400):
            fail = rng.random() < 0.15
            obs.event("denied" if fail else "ok", fail, 12.0)
        self.assertTrue(obs.cusum.alarm)
        self.assertLessEqual(len(obs.reservoir.items), 32)

    def test_control_often_quiet(self) -> None:
        random.seed(3)
        alarms = 0
        trials = 20
        for t in range(trials):
            rng = random.Random(100 + t)
            obs = Observe(reservoir_k=16)
            for _ in range(3400):
                fail = rng.random() < 0.01
                obs.event("denied" if fail else "ok", fail, 10.0)
            if obs.cusum.alarm:
                alarms += 1
        self.assertLess(alarms, 5)
        print(f"P0 control p=0.01: {alarms}/{trials} alarmed over 3400 events each")

    def test_memory_fixed(self) -> None:
        obs = Observe(reservoir_k=32)
        m0 = obs.memory_cells()
        for i in range(5000):
            obs.event("denied", True, 1.0)
        self.assertEqual(obs.memory_cells(), m0)


if __name__ == "__main__":
    unittest.main()
