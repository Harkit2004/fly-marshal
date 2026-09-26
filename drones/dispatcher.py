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

STANDOFF_M = 18.0
HOLD_ALT = 22.0
ESCORT_BACK_M = 25.0
VERSUS_STACK_M = 14.0


class Dispatcher:
    def __init__(self, track: Track, drones: list[Drone], versus: bool = True):
        self.track = track
        self.drones = drones
        self.versus = versus          # also send the fly drone to every incident (Fly vs Code)
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
        fly = next((d for d in free if d.pilot == "fly"), None)
        if self.versus and event["type"] == "incident" and fly:
            # Fly vs Code: nearest conventional drone + the fly drone race to the same spot
            pid = [d for d in free if d.pilot != "fly"]
            sent = ([min(pid, key=lambda d: self.eta(d, p))] if pid else []) + [fly]
        else:
            sent = [min(free, key=lambda d: self.eta(d, p))]
        mode = "hold" if event["type"] == "incident" else "intercept"
        for d in sent:
            # stack drones vertically so the safety layer doesn't have to separate them
            lift = np.array([0.0, VERSUS_STACK_M if d.pilot == "fly" and len(sent) > 1 else 0.0, 0.0])
            d.mode, d.event_id, d.target, d.follow_car = mode, event["id"], p + lift, None
        self.events[event["id"]] = {"event": event, "drones": [d.id for d in sent]}
        return [d.id for d in sent]

    def reinforce(self, event: dict) -> list[int]:
        """Send newly freed drones to an active incident that is short of drones.
        In versus mode the fly drone always joins; otherwise top up to one drone."""
        info = self.events.get(event["id"])
        if info is None or event["type"] != "incident":
            return []
        mine = [d for d in self.drones if d.event_id == event["id"]]
        free = [d for d in self.drones if d.mode == "patrol"]
        if not free:
            return []
        p = self.track.standoff(event["track_pos"], STANDOFF_M, HOLD_ALT)
        send = []
        if self.versus and not any(d.pilot == "fly" for d in mine):
            send += [d for d in free if d.pilot == "fly"][:1]
        if not any(d.pilot != "fly" for d in mine):
            pid = [d for d in free if d.pilot != "fly"]
            if pid:
                send.append(min(pid, key=lambda d: self.eta(d, p)))
        for d in send:
            lift = np.array([0.0, VERSUS_STACK_M if d.pilot == "fly" else 0.0, 0.0])
            d.mode, d.event_id, d.target, d.follow_car = ("escort" if any(m.mode == "escort" for m in mine) else "hold"), event["id"], p + lift, None
            if d.mode == "escort":
                d.follow_car = event["car_ids"][0]
            info["drones"].append(d.id)
        return [d.id for d in send]

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
                d.target = self.track.standoff(tp, STANDOFF_M, HOLD_ALT + (VERSUS_STACK_M if d.pilot == "fly" else 0))
            elif d.mode == "patrol":
                d.target = self.patrol_point(d)
            out[d.id] = d.target
        return out
