"""Session-local speed reference, independent of the trained crash models.

One median per car/lap/section, never one vote per telemetry tick. The first
observed lap is discarded, including when attaching halfway through a session.
"""
from collections import defaultdict, deque
import math
from statistics import median

from shared.settings import get


class LiveSpeedReference:
    def __init__(self, length):
        self.enabled = bool(get('live_speed.enabled', False))
        self.length = max(1., length)
        self.bins = max(1, math.ceil(self.length / float(get('live_speed.section_m', 50))))
        self.minimum = int(get('live_speed.min_passes', 5))
        self.samples = defaultdict(lambda: deque(maxlen=int(get('live_speed.history_passes', 40))))
        self.first_lap = {}
        self.pending = {}
        self.voted = {}
        self.started = None
        self.last_t = None

    def key(self, car):
        # Without model metadata, learn that car alone instead of mixing classes.
        return (car.get('car_model') or f"unknown:{car['car_id']}",
                int((float(car['track_pos']) % 1) * self.bins))

    def expected(self, car):
        values = self.samples.get(self.key(car), ())
        return median(values) if len(values) >= self.minimum else None

    def ready(self, t, car):
        lap = car.get('lap')
        return (lap is not None and car['car_id'] in self.first_lap
                and lap > self.first_lap[car['car_id']]
                and self.started is not None
                and t - self.started >= float(get('live_speed.start_grace_s', 45)))

    def is_slow(self, car):
        reference = self.expected(car)
        speed = car['speed_kmh']
        return (reference is not None and speed > 5
                and speed < reference * float(get('live_speed.slow_ratio', .65))
                and reference - speed >= float(get('live_speed.min_deficit_kmh', 30)))

    def recovered(self, car):
        reference = self.expected(car)
        return reference is None or car['speed_kmh'] >= reference * float(get('live_speed.recovery_ratio', .8))

    def update(self, t, cars, feats, excluded=(), events=()):
        if not self.enabled:
            return
        if self.last_t is not None and t < self.last_t:
            self.samples.clear(); self.first_lap.clear(); self.pending.clear(); self.voted.clear(); self.started = None
        self.last_t = t
        if self.started is None:
            self.started = t
        present = {c['car_id'] for c in cars}
        self.pending = {i: p for i, p in self.pending.items() if i in present}
        hazards = [e['track_pos'] for e in events if e['type'] == 'incident']
        before = float(get('scene.yellow_before_m', 200)) + float(get('scene.yellow_ai_approach_m', 250))
        after = float(get('scene.yellow_after_m', 50))
        for c in cars:
            cid, lap = c['car_id'], c.get('lap')
            if lap is not None:
                self.first_lap.setdefault(cid, lap)
            key = self.key(c)
            token = (lap, key)
            prior = self.pending.get(cid)
            if prior and prior['token'] != token:
                old_lap, old_key = prior['token']
                vote_lap, sections = self.voted.get(cid, (old_lap, set()))
                if vote_lap != old_lap:
                    sections = set()
                if prior['valid'] and prior['speeds'] and old_key not in sections:
                    self.samples[prior['token'][1]].append(median(prior['speeds']))
                    sections.add(old_key)
                self.voted[cid] = (old_lap, sections)
                prior = None
            if prior is None:
                prior = self.pending[cid] = dict(token=token, valid=True, speeds=[])
            f = feats.get(cid, {})
            pos = float(c['track_pos']) % 1
            blocked = any(((p-pos) % 1)*self.length <= before
                          or ((pos-p) % 1)*self.length <= after for p in hazards)
            reference = self.expected(c)
            speed = c['speed_kmh']
            clean = (self.ready(t,c) and cid not in excluded and not c.get('in_pit')
                     and not blocked and math.isfinite(speed) and speed >= 20
                     and f.get('wheels_out',0) < 3 and abs(f.get('yaw_excess',0)) < .7
                     and f.get('accel',0) > -8
                     and (reference is None or speed >= reference * .8))
            prior['valid'] = prior['valid'] and clean
            if clean and len(prior['speeds']) < 200:
                prior['speeds'].append(speed)
