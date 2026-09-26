"""Pilots turn "go to this target" into a velocity command.

PIDPilot      conventional controller: the "Code" side of Fly vs Code.
FlyBrainPilot beacon adapter (target -> fake optic flow -> forward/turn/climb -> velocity)
              driving either the real FlyWire connectome (drones/flybrain_real.py,
              needs the connectome CSVs) or PlaceholderFlyBrain when that isn't set up.

Both brains expose step([forward, left, right, vertical]) -> {forward, turn, climb, spikes}.
Measure steps/sec on the GPU; if < FLY_HZ, lower FLY_HZ (outputs are held between steps).

STEER_ASSIST blends the fly's turn output with the beacon bearing. The flybrain README
notes its left/right photoreceptor split is an arbitrary proxy, so the real connectome
may not turn toward the target by itself. Start at 0.0, raise it only if it can't steer,
and say so in the pitch ("the fly flies, we nudge its heading").
"""

from __future__ import annotations

import math

import numpy as np

from drones.sim import Drone
from shared.config import DRONE_MAX_SPEED


class PIDPilot:
    def __init__(self, kp: float = 0.6, kd: float = 0.9):
        self.kp, self.kd = kp, kd

    def command(self, d: Drone, target: np.ndarray, dt: float) -> np.ndarray:
        err = target - d.pos
        cmd = self.kp * err - self.kd * d.vel * 0.1
        # slow down smoothly when close so it doesn't overshoot
        dist = np.linalg.norm(err)
        max_sp = min(DRONE_MAX_SPEED, 0.8 * math.sqrt(2 * 8.0 * max(dist, 0.01)))
        n = np.linalg.norm(cmd)
        if n > max_sp:
            cmd *= max_sp / n
        d.activity = {}
        return cmd


class PlaceholderFlyBrain:
    """Stands in for the connectome: a noisy, twitchy steering reflex.

    Left/right imbalance in optic flow -> turn toward the stronger side,
    vertical flow -> climb, forward flow -> thrust. Deliberately a bit wobbly.
    """

    def __init__(self, seed: int = 0):
        self.rng = np.random.default_rng(seed)

    def step(self, flow: list[float]) -> dict:
        fwd, left, right, vert = flow
        turn = np.tanh(3.0 * (left - right)) + self.rng.normal(0, 0.08)
        climb = np.tanh(2.0 * vert) + self.rng.normal(0, 0.05)
        forward = np.clip(fwd * (1.0 - 0.6 * abs(turn)), 0, 1)
        spikes = int(30000 + 8000 * (abs(turn) + abs(climb) + forward) + self.rng.integers(0, 3000))
        return {"forward": float(forward), "turn": float(turn), "climb": float(climb), "spikes": spikes}


class FlyBrainPilot:
    FLY_HZ = 15.0
    TURN_RATE = 2.2        # rad/s at full turn
    CLIMB_SPEED = 6.0      # m/s at full climb

    def __init__(self, brain=None, steer_assist: float = 0.0):
        self.brain = brain or PlaceholderFlyBrain()
        self.steer_assist = steer_assist
        self.out = {"forward": 0.0, "turn": 0.0, "climb": 0.0, "spikes": 0}
        self.acc = 0.0

    @staticmethod
    def beacon_flow(d: Drone, target: np.ndarray) -> list[float]:
        """Target position relative to the drone -> [forward, left, right, vertical] in 0..1."""
        rel = target - d.pos
        horiz = math.hypot(rel[0], rel[2])
        bearing = math.atan2(rel[2], rel[0]) - d.yaw
        bearing = (bearing + math.pi) % (2 * math.pi) - math.pi      # + = target to the left
        elev = math.atan2(rel[1], max(horiz, 1.0))
        forward = min(1.0, horiz / 150.0) * max(0.0, math.cos(bearing))
        left = max(0.0, math.sin(bearing)) + (0.5 if abs(bearing) > math.pi / 2 and bearing > 0 else 0)
        right = max(0.0, -math.sin(bearing)) + (0.5 if abs(bearing) > math.pi / 2 and bearing <= 0 else 0)
        return [forward, min(left, 1.0), min(right, 1.0), max(-1.0, min(1.0, elev * 2))]

    def command(self, d: Drone, target: np.ndarray, dt: float) -> np.ndarray:
        self.acc += dt
        if self.acc >= 1.0 / self.FLY_HZ:
            self.acc = 0.0
            self.out = self.brain.step(self.beacon_flow(d, target))
        o = self.out
        turn = o["turn"]
        if self.steer_assist > 0:
            rel = target - d.pos
            bearing = (math.atan2(rel[2], rel[0]) - d.yaw + math.pi) % (2 * math.pi) - math.pi
            turn = (1 - self.steer_assist) * turn + self.steer_assist * math.tanh(2 * bearing)
        d.yaw += turn * self.TURN_RATE * dt
        dist = float(np.linalg.norm(target - d.pos))
        speed = o["forward"] * DRONE_MAX_SPEED * min(1.0, dist / 40.0)
        d.activity = {k: round(v, 3) if isinstance(v, float) else v for k, v in o.items()}
        return np.array([math.cos(d.yaw) * speed, o["climb"] * self.CLIMB_SPEED, math.sin(d.yaw) * speed])
