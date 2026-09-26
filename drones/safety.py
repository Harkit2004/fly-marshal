"""Deterministic safety layer between every pilot and the drone.

Pilots (including the fly) only suggest a velocity. This layer can always
override it: altitude band, drone-to-drone separation, and keeping clear of
the racing surface. Keep this as plain code, never ML.
"""

from __future__ import annotations

import numpy as np

from drones.sim import Drone
from shared.config import (DRONE_MAX_ALT, DRONE_MIN_ALT, DRONE_MIN_SEPARATION,
                           DRONE_TRACK_CLEARANCE)
from shared.track import Track


class SafetyLayer:
    def __init__(self, track: Track):
        self.track = track
        self.interventions = 0

    def filter(self, drones: list[Drone], cmds: dict[int, np.ndarray]) -> dict[int, np.ndarray]:
        out = {}
        for d in drones:
            v = cmds[d.id].copy()
            i, dist = self.track.nearest(d.pos[0], d.pos[2])
            ground = self.track.y[i]
            alt = d.pos[1] - ground

            # altitude band
            if alt < DRONE_MIN_ALT:
                v[1] = max(v[1], 3.0 * (DRONE_MIN_ALT - alt))
                self.interventions += 1
            elif alt > DRONE_MAX_ALT:
                v[1] = min(v[1], -3.0 * (alt - DRONE_MAX_ALT))

            # stay off the racing surface: push outward if horizontally too close
            if dist < DRONE_TRACK_CLEARANCE:
                push = self.track.outward[i] * (DRONE_TRACK_CLEARANCE - dist) * 2.0
                v[0] += push[0]
                v[2] += push[1]
                self.interventions += 1

            # separation from other drones
            for o in drones:
                if o.id == d.id:
                    continue
                rel = d.pos - o.pos
                n = np.linalg.norm(rel)
                if 0 < n < DRONE_MIN_SEPARATION:
                    v += rel / n * (DRONE_MIN_SEPARATION - n) * 2.0
                    self.interventions += 1
            out[d.id] = v
        return out
