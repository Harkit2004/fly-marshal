"""Point-mass drone simulation in AC world coordinates (metres, y up)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from shared.config import DRONE_MAX_ACCEL, DRONE_MAX_SPEED
from shared.schemas import DroneState


@dataclass
class Drone:
    id: int
    pilot: str                          # "fly" | "pid"
    pos: np.ndarray
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3))
    yaw: float = 0.0                    # heading in the x/z plane (rad), used by the fly pilot
    mode: str = "patrol"
    target: np.ndarray | None = None
    event_id: str | None = None
    follow_car: int | None = None
    activity: dict = field(default_factory=dict)

    def step(self, cmd_vel: np.ndarray, dt: float) -> None:
        dv = cmd_vel - self.vel
        n = np.linalg.norm(dv)
        if n > DRONE_MAX_ACCEL * dt:
            dv *= DRONE_MAX_ACCEL * dt / n
        self.vel = self.vel + dv
        sp = np.linalg.norm(self.vel)
        if sp > DRONE_MAX_SPEED:
            self.vel *= DRONE_MAX_SPEED / sp
        self.pos = self.pos + self.vel * dt
        if np.hypot(self.vel[0], self.vel[2]) > 0.5 and self.pilot != "fly":
            self.yaw = float(np.arctan2(self.vel[2], self.vel[0]))

    def state(self, t: float) -> DroneState:
        return DroneState(
            t=t, drone_id=self.id, pilot=self.pilot,
            x=float(self.pos[0]), y=float(self.pos[1]), z=float(self.pos[2]),
            vx=float(self.vel[0]), vy=float(self.vel[1]), vz=float(self.vel[2]),
            mode=self.mode,
            target=None if self.target is None else tuple(round(float(v), 2) for v in self.target),
            event_id=self.event_id, fly_activity=self.activity,
        )
