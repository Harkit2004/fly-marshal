import unittest
from ml.live_speed import LiveSpeedReference


def car(i=0, lap=1, pos=.025, speed=140, model='gt3'):
    return dict(car_id=i, lap=lap, track_pos=pos, speed_kmh=speed, car_model=model)


class LiveSpeedTests(unittest.TestCase):
    def setUp(self):
        self.ref = LiveSpeedReference(1000)
        self.ref.enabled = True
        self.cars = [car(i, lap=0) for i in range(5)]
        self.ref.update(0,self.cars,{})

    def train(self):
        # End first observed lap, enter a new section, then finish a clean pass.
        self.cars = [car(i) for i in range(5)]
        self.ref.update(50,self.cars,{})
        self.ref.update(51,[car(i,pos=.075) for i in range(5)],{})

    def test_start_lap_and_grace_never_train_or_detect(self):
        self.ref.update(20,self.cars,{})
        self.ref.update(21,[car(i,lap=0,pos=.075) for i in range(5)],{})
        self.assertIsNone(self.ref.expected(car()))
        self.assertFalse(self.ref.ready(100, car(lap=0)))
        self.assertFalse(self.ref.ready(10, car()))

    def test_majority_per_model_reference_and_relative_slowdown(self):
        self.train()
        self.assertEqual(self.ref.expected(car()),140)
        self.assertTrue(self.ref.is_slow(car(speed=60)))
        self.assertFalse(self.ref.is_slow(car(speed=120)))
        self.assertIsNone(self.ref.expected(car(model='mx5')))

    def test_blocked_laps_cannot_redefine_normal(self):
        self.train()
        for lap in range(2,15):
            self.ref.update(100+lap*2,[car(i,lap=lap,speed=40) for i in range(5)],{})
            self.ref.update(101+lap*2,[car(i,lap=lap,pos=.075,speed=40) for i in range(5)],{})
        self.assertEqual(self.ref.expected(car()),140)
        self.assertTrue(self.ref.is_slow(car(speed=40)))

    def test_yellows_incidents_and_pits_do_not_train(self):
        for reason in ('yellow','hazard','pit'):
            with self.subTest(reason=reason):
                self.setUp()
                cars=[car(i) for i in range(5)]
                if reason=='pit':
                    for c in cars: c['in_pit']=True
                self.ref.update(50,cars,{},set(range(5)) if reason=='yellow' else (),
                                [dict(type='incident',track_pos=.1)] if reason=='hazard' else ())
                self.ref.update(51,[car(i,pos=.075) for i in range(5)],{})
                self.assertIsNone(self.ref.expected(car()))

    def test_one_vote_per_pass_even_with_boundary_jitter(self):
        self.train()
        for t in range(52,60):
            self.ref.update(t,[car(i,pos=.025 if t%2==0 else .075) for i in range(5)],{})
        self.assertEqual(len(self.ref.samples[self.ref.key(car())]),5)

    def test_disabled_and_time_reset(self):
        self.train()
        self.ref.update(0,[car(lap=0)],{})
        self.assertIsNone(self.ref.expected(car()))
        self.ref.enabled=False
        self.train()
        self.assertIsNone(self.ref.expected(car()))

    def test_relative_rule_has_no_old_100_kmh_ceiling(self):
        self.train()
        self.ref.samples[self.ref.key(car())].clear()
        self.ref.samples[self.ref.key(car())].extend([240,240,240,240,60])
        self.assertEqual(self.ref.expected(car()),240)
        self.assertTrue(self.ref.is_slow(car(speed=130)))
        self.assertFalse(self.ref.is_slow(car(speed=220)))
