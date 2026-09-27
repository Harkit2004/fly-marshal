import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from brain import Brain
from shared.track import Track
from shared.telemetry import telemetry_messages
from drones.pilots import PIDPilot
from drones.sim import Drone


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        rows = [[i/100, 500*np.cos(i*np.pi/50), 0, 500*np.sin(i*np.pi/50), 200] for i in range(100)]
        with patch('brain.AnomalyDetector'), patch('brain.RiskPredictor'):
            self.brain = Brain(Track(rows), versus=True)
        self.addCleanup(self.brain.close)
        self.car = dict(car_id=0, x=500., y=0., z=0., track_pos=0., speed_kmh=0., in_pit=False)
        self.feat = dict(speed_kmh=0., speed_deficit_kmh=200., yaw_rate=0., accel=0., wheels_out=0, off_line_m=0.)
        self.brain.features.update = lambda t, cars: {c['car_id']: self.feat for c in cars}
        self.brain.anomaly.score_many = lambda feats: [(0.9, 'stopped') for _ in feats]
        self.brain.risk.predict_many = lambda feats, scores: [None for _ in feats]

    def start_incident(self):
        for t in (20., 20.1, 20.2): self.brain.tick(t, [self.car])
        self.assertIn(0, self.brain.active)

    def test_one_drone_even_with_legacy_versus_and_reinforcement(self):
        self.start_incident()
        for t in (21., 22., 23.): self.brain.tick(t, [self.car])
        self.assertEqual(sum(d.event_id is not None for d in self.brain.drones), 1)

    def test_departed_car_clears_even_when_model_window_still_hot(self):
        self.start_incident()
        self.car.update(x=440., z=100., track_pos=.04, speed_kmh=160.)
        self.feat.update(speed_kmh=160., speed_deficit_kmh=40.)
        self.brain.tick(20.3, [self.car])
        self.assertEqual(self.brain.active[0]['x'], 440.)
        self.brain.tick(21.1, [self.car])
        self.assertNotIn(0, self.brain.active)
        self.assertTrue(all(d.mode == 'patrol' for d in self.brain.drones))

    def test_stationary_hazard_stays_and_missing_car_releases(self):
        self.start_incident()
        self.brain.tick(25., [self.car])
        self.assertIn(0, self.brain.active)
        self.brain.tick(25.1, [])
        self.assertFalse(self.brain.active)

    def test_warning_is_rescored_and_target_tracks_car(self):
        self.brain.anomaly.score_many = lambda feats: [(0., None) for _ in feats]
        self.brain.risk.predict_many = lambda feats, scores: [(.8, 5.) for _ in feats]
        self.brain.tick(20., [self.car])
        owner = next(d for d in self.brain.drones if d.event_id)
        before = owner.target.copy()
        self.car.update(track_pos=.2, x=150., z=470.)
        self.brain.tick(20.1, [self.car])
        self.assertFalse(np.allclose(before, owner.target))
        self.brain.risk.predict_many = lambda feats, scores: [None for _ in feats]
        self.brain.tick(20.2, [self.car])
        self.brain.tick(21., [self.car])
        self.assertFalse(self.brain.active)

    def test_fast_pid_uses_configured_acceleration(self):
        drone = Drone(0, 'pid', np.zeros(3))
        velocity = PIDPilot().command(drone, np.array([1000., 0., 0.]), .1)
        self.assertGreater(np.linalg.norm(velocity), 110.)

    def test_slow_car_without_ml_alert_gets_one_persistent_escort(self):
        self.brain.anomaly.score_many = lambda feats: [(0., None) for _ in feats]
        self.feat.update(speed_kmh=50., speed_deficit_kmh=150.)
        self.car['speed_kmh'] = 50.
        self.brain.tick(20., [self.car])
        self.brain.tick(21., [self.car])
        self.assertFalse(self.brain.active)
        self.brain.tick(22.1, [self.car])
        event = self.brain.active[0]
        self.assertEqual(event['kind'], 'limp')
        owner = next(d for d in self.brain.drones if d.event_id)
        target = owner.target.copy()
        self.car.update(x=0., z=500., track_pos=.25)
        self.brain.tick(23., [self.car])
        self.brain.tick(115., [self.car])
        self.assertEqual(sum(d.mode == 'escort' for d in self.brain.drones), 1)
        self.assertFalse(np.allclose(target, owner.target))
        self.feat.update(speed_kmh=190., speed_deficit_kmh=10.)
        self.car['speed_kmh'] = 190.
        self.brain.tick(116., [self.car])
        self.brain.tick(117., [self.car])
        self.assertFalse(self.brain.active)
        self.assertEqual(owner.mode, 'patrol')

    def test_normal_corner_braking_and_pits_do_not_trigger_slow_escort(self):
        self.brain.anomaly.score_many = lambda feats: [(0., None) for _ in feats]
        for changes in (dict(speed_kmh=50., speed_deficit_kmh=10., accel=0.),
                        dict(speed_kmh=50., speed_deficit_kmh=150., accel=-5.)):
            self.feat.update(changes)
            for t in (20., 21., 23.): self.brain.tick(t, [self.car])
            self.assertFalse(self.brain.active)
        self.feat.update(speed_kmh=50., speed_deficit_kmh=150., accel=0.)
        self.car['in_pit'] = True
        for t in (24., 25., 27.): self.brain.tick(t, [self.car])
        self.assertFalse(self.brain.active)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_bursts_coalesce_without_losing_track(self):
        class Socket:
            async def __aiter__(self):
                yield json.dumps({'type':'session_reset'})
                yield json.dumps({'type':'track', 'source':'live'})
                for t in range(100): yield json.dumps({'type':'frames', 't':t})
        messages = [m async for m in telemetry_messages(Socket())]
        self.assertEqual([m['type'] for m in messages], ['session_reset','track','frames'])
        self.assertEqual(messages[-1]['t'], 99)

    async def test_replay_keeps_each_frame(self):
        class Socket:
            async def __aiter__(self):
                yield json.dumps({'type':'track', 'source':'replay'})
                for t in range(4): yield json.dumps({'type':'frames', 't':t})
        messages = [m async for m in telemetry_messages(Socket())]
        self.assertEqual(len(messages), 5)
