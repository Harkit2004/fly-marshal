"""Assigns drones to events and computes each drone's target every tick.

Modes:
  patrol     hover beside the middle of its own sector
  intercept  go to a predicted risk location before it happens
  hold       hover over an incident while vision and race control watch
  escort     follow a slow / damaged car at an offset
"""

from __future__ import annotations

import numpy as np

from drones.sim import Drone
from shared.config import DRONE_MAX_SPEED, DRONE_PATROL_ALT
from shared.track import Track

from shared.settings import get

# tune in settings.toml [drones]
STANDOFF_M = float(get("drones.standoff_m"))
HOLD_ALT = float(get("drones.hold_alt_m"))
ESCORT_BACK_M = float(get("drones.escort_back_m"))
VERSUS_STACK_M = float(get("drones.versus_stack_m"))


class Dispatcher:
    def __init__(self, track: Track, drones: list[Drone], versus: bool = False):
        self.track = track
        self.drones = drones
        self.versus = False           # compatibility argument; operational dispatch is one drone per car
        n = len(drones)
        self.sector_tp = {d.id: (k + 0.5) / n for k, d in enumerate(drones)}
        self.events: dict[str, dict] = {}

    def patrol_point(self, d: Drone) -> np.ndarray:
        return self.track.standoff(self.sector_tp[d.id], STANDOFF_M, DRONE_PATROL_ALT)

    def eta(self, d: Drone, p: np.ndarray) -> float:
        return float(np.linalg.norm(p - d.pos)) / DRONE_MAX_SPEED

    def assign(self, event: dict) -> list[int]:
        """event: RiskEvent dict. Returns the drone ids sent."""
        if event["id"] in self.events:
            return []
        p = self.track.standoff(event["track_pos"], STANDOFF_M, HOLD_ALT)
        free = [d for d in self.drones if d.mode == "patrol"]
        # an incident may take a drone that is only intercepting a prediction
        if not free and event["type"] == "incident":
            free = [d for d in self.drones if d.mode == "intercept"]
        if not free:
            return []
        # One owner per car, including warning -> incident transitions.
        for old_id, info in list(self.events.items()):
            if set(info["event"]["car_ids"]) & set(event["car_ids"]):
                self.release(old_id)
        sent = [min(free, key=lambda d: self.eta(d, p))]
        for d in sent:
            if d.event_id:
                self.release(d.event_id)
        mode = "hold" if event["type"] == "incident" else "intercept"
        for d in sent:
            d.mode, d.event_id, d.target, d.follow_car = mode, event["id"], p, None
        self.events[event["id"]] = {"event": event, "drones": [d.id for d in sent]}
        return [d.id for d in sent]

    def reinforce(self, event: dict) -> list[int]:
        """Retry unassigned events without adding a second drone."""
        if any(d.event_id == event["id"] for d in self.drones):
            return []
        self.events.pop(event["id"], None)
        return self.assign(event)

    def escort(self, event_id: str, car_id: int) -> None:
        for d in self.drones:
            if d.event_id == event_id:
                d.mode, d.follow_car = "escort", car_id

    def release(self, event_id: str) -> None:
        for d in self.drones:
            if d.event_id == event_id:
                d.mode, d.event_id, d.follow_car = "patrol", None, None
        self.events.pop(event_id, None)

    def targets(self, cars: dict[int, dict]) -> dict[int, np.ndarray]:
        out = {}
        for d in self.drones:
            if d.mode == "escort" and d.follow_car in cars:
                c = cars[d.follow_car]
                tp = self.track.advance(c["track_pos"], -ESCORT_BACK_M)
                d.target = self.track.standoff(tp, STANDOFF_M, HOLD_ALT)
            elif d.event_id in self.events:
                event = self.events[d.event_id]["event"]
                c = cars.get(event["car_ids"][0])
                if c:
                    d.target = self.track.standoff(c["track_pos"], STANDOFF_M, HOLD_ALT)
            elif d.mode == "patrol":
                d.target = self.patrol_point(d)
            out[d.id] = d.target
        return out
