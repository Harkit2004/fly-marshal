"""The brain: telemetry in, risk events + drone states out.

  ws://localhost:8765 (replay_stream)  ->  features -> detectors -> events
  -> dispatcher -> pilots (fly / pid) -> safety layer -> sim
  ->  ws://localhost:8766 (dashboard)

Usage: python brain.py [--no-versus] [--steer-assist 0.5]
  Real connectome: set FLYBRAIN_DATA to the folder with fly_neurons_real.csv and
  fly_synapses_real.csv (see drones/flybrain_real.py). Without it a placeholder fly is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import numpy as np
from websockets.asyncio.client import connect
from websockets.asyncio.server import broadcast, serve

from cv.report import telemetry_report
from drones.dispatcher import Dispatcher
from drones.flybrain_real import RealFlyBrainAdapter
from drones.pilots import FlyBrainPilot, PIDPilot
from drones.safety import SafetyLayer
from drones.sim import Drone
from ml.detectors import AnomalyDetector, RiskPredictor
from ml.features import OnlineFeatures
from shared.config import BRAIN_WS_PORT, DATA, DRONE_COUNT, DRONE_PATROL_ALT, TELEMETRY_WS_PORT
from shared.schemas import RiskEvent, message
from shared.track import Track

ANOMALY_ON = 0.5
ANOMALY_TICKS = 8           # ~0.5 s at 15 Hz before an incident is raised
PREDICT_TTL_S = 8.0         # a prediction expires this long after its eta
INCIDENT_MAX_S = 90.0


class Brain:
    def __init__(self, track: Track, versus: bool, fly_brain=None, steer_assist: float = 0.0):
        self.track = track
        self.features = OnlineFeatures(track)
        self.anomaly = AnomalyDetector(DATA.parent / "models" / "anomaly.pkl")
        self.risk = RiskPredictor(DATA.parent / "models" / "risk.pkl")
        self.drones = []
        for k in range(DRONE_COUNT):
            pilot = "fly" if k == 0 else "pid"
            start = track.standoff((k + 0.5) / DRONE_COUNT, 18.0, DRONE_PATROL_ALT)
            self.drones.append(Drone(id=k, pilot=pilot, pos=start))
        self.pilots = {d.id: FlyBrainPilot(fly_brain, steer_assist) if d.pilot == "fly" else PIDPilot()
                       for d in self.drones}
        self.safety = SafetyLayer(track)
        self.dispatch = Dispatcher(track, self.drones, versus=versus)
        self.hot: dict[int, int] = {}                  # car -> consecutive anomalous ticks
        self.active: dict[int, dict] = {}              # car -> active event dict
        self.slow: dict[int, int] = {}                 # car -> consecutive "limping" ticks
        self.last_t: float | None = None

    def kind_of(self, f: dict) -> str:
        if abs(f["yaw_rate"]) > 1.2 and f["speed_deficit_kmh"] > 40:
            return "spin"
        if f["speed_kmh"] < 30:
            return "stopped"
        if f.get("wheels_out", 0) >= 3 or f["off_line_m"] > 15:
            return "off-track"
        return "anomaly"

    def new_event(self, t, typ, kind, car, c, severity, eta=0.0) -> dict:
        ev = RiskEvent(id=f"{typ[:3]}-{car}-{int(t)}", t=t, type=typ, kind=kind, car_ids=[car],
                       x=c["x"], y=c["y"], z=c["z"], track_pos=c["track_pos"],
                       severity=round(severity, 2), eta_s=round(eta, 1))
        return ev.__dict__

    def tick(self, t: float, cars: list[dict]) -> list[str]:
        msgs: list[str] = []
        dt = 0.05 if self.last_t is None or not 0 < t - self.last_t < 1 else t - self.last_t
        self.last_t = t
        byid = {c["car_id"]: c for c in cars}
        feats = self.features.update(t, cars)

        for car, f in feats.items():
            c = byid[car]
            if c.get("in_pit") or t < 15:
                # a car that reaches the pits (or is removed there by AC) is no longer a track hazard
                if car in self.active and c.get("in_pit"):
                    self.end(self.active[car], msgs)
                continue
            score = self.anomaly.score(f)
            self.hot[car] = self.hot.get(car, 0) + 1 if score > ANOMALY_ON else 0
            act = self.active.get(car)

            # reactive: sustained anomaly -> incident
            if self.hot[car] >= ANOMALY_TICKS and (act is None or act["type"] == "predicted"):
                if act:
                    self.end(act, msgs)
                ev = self.new_event(t, "incident", self.kind_of(f), car, c, score)
                self.active[car] = ev
                self.dispatch.assign(ev)
                msgs.append(message("risk", event=ev))
                msgs.append(message("report", report=telemetry_report(ev, f)))
                continue

            # predictive: fast car closing on a slow one
            if act is None:
                sustained = score if self.hot[car] >= 3 else 0.0
                pred = self.risk.predict(car, f, sustained)
                if pred:
                    p, eta = pred
                    ev = self.new_event(t, "predicted", "closing", car, c, p, eta)
                    self.active[car] = ev
                    self.dispatch.assign(ev)
                    msgs.append(message("risk", event=ev))
                continue

            # lifecycle of active events
            age = t - act["t"]
            if act["type"] == "predicted" and age > act["eta_s"] + PREDICT_TTL_S:
                self.end(act, msgs)
            elif act["type"] == "incident":
                # limping = moving slowly without decelerating, sustained for 2 s
                moving_slow = 20 < f["speed_kmh"] < 100 and f["speed_deficit_kmh"] > 60 and f["accel"] > -1
                self.slow[car] = self.slow.get(car, 0) + 1 if moving_slow else 0
                gone_m = abs((c["track_pos"] - act["track_pos"] + 0.5) % 1.0 - 0.5) * self.track.length
                recovered = f["speed_deficit_kmh"] < 30 or (gone_m > 250 and f["speed_deficit_kmh"] < 60)
                if self.slow[car] >= 30 and act["kind"] != "limp":
                    act["kind"] = "limp"
                    self.dispatch.escort(act["id"], car)
                    msgs.append(message("risk", event=act))
                elif recovered or age > INCIDENT_MAX_S:
                    self.end(act, msgs)

        # freed drones top up incidents that are still active
        for ev in {id(e): e for e in self.active.values()}.values():
            self.dispatch.reinforce(ev)

        # drones
        targets = self.dispatch.targets(byid)
        cmds = {d.id: self.pilots[d.id].command(d, targets[d.id], dt) for d in self.drones}
        cmds = self.safety.filter(self.drones, cmds)
        for d in self.drones:
            d.step(cmds[d.id], dt)
        # active events ride along every tick so a dashboard that (re)connects mid-race is in sync
        active = list({id(e): e for e in self.active.values()}.values())
        msgs.append(message("drones", t=t, drones=[d.state(t) for d in self.drones], events=active))
        return msgs

    def end(self, ev: dict, msgs: list[str]) -> None:
        self.dispatch.release(ev["id"])
        for car in ev["car_ids"]:
            if self.active.get(car) is ev:
                del self.active[car]
        msgs.append(json.dumps({"type": "risk_end", "id": ev["id"]}))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-versus", action="store_true", help="don't also send the fly drone to incidents")
    ap.add_argument("--steer-assist", type=float, default=0.0, help="0-1, blend beacon bearing into the fly's turn")
    args = ap.parse_args()
    fly_brain = RealFlyBrainAdapter.load()     # None -> placeholder fly

    async def handler(ws):
        await ws.wait_closed()

    async with serve(handler, "localhost", BRAIN_WS_PORT) as server:
        print(f"brain publishing on ws://localhost:{BRAIN_WS_PORT}")
        while True:
            try:
                async with connect(f"ws://localhost:{TELEMETRY_WS_PORT}", max_size=None) as tel:
                    print("connected to telemetry")
                    brain = None
                    async for raw in tel:
                        m = json.loads(raw)
                        if m["type"] == "track":
                            brain = Brain(Track(m["centerline"]), versus=not args.no_versus,
                                          fly_brain=fly_brain, steer_assist=args.steer_assist)
                            print(f"track loaded: {brain.track.length:.0f} m")
                        elif m["type"] == "frames" and brain:
                            for out in brain.tick(m["t"], m["cars"]):
                                if '"risk' in out[:20] or '"report' in out[:20]:
                                    print(out[:160])
                                broadcast(server.connections, out)
            except (OSError, Exception) as e:  # telemetry not up yet, or stream ended
                print(f"waiting for telemetry ({type(e).__name__})")
                await asyncio.sleep(2)


if __name__ == "__main__":
    np.set_printoptions(precision=2, suppress=True)
    asyncio.run(main())
