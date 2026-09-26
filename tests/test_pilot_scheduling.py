import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from drones.pilots import FlyBrainPilot
from shared.settings import SETTINGS


class PilotSchedulingTests(unittest.TestCase):
    def test_bursts_are_coalesced_and_close_stops_worker(self):
        calls, first, second = [], threading.Event(), threading.Event()

        def step(flow):
            calls.append(time.perf_counter())
            (first if len(calls) == 1 else second).set()
            return dict(forward=1., turn=0., climb=0., spikes=1)

        with patch.dict(SETTINGS, {'flybrain': {'max_hz': 10}}):
            pilot = FlyBrainPilot(SimpleNamespace(step=step))
        self.addCleanup(pilot.close)
        drone = SimpleNamespace(pos=np.array([0., 25., 0.]), yaw=0.)
        target = np.array([100., 25., 0.])
        pilot.command(drone, target, .01)
        self.assertTrue(first.wait(2))
        for _ in range(100):
            pilot.command(drone, target, .01)
        self.assertTrue(second.wait(2))
        pilot.close()
        self.assertFalse(pilot._thread.is_alive())
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(calls[1] - calls[0], .09)


if __name__ == '__main__': unittest.main()
