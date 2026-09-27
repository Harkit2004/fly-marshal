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
import time

import numpy as np
from websockets.asyncio.client import connect
from websockets.asyncio.server import broadcast, serve

from cv.report import telemetry_report
from cv.worker import VisionWorker
from drones.game_feeds import GameFeeds
from drones.caution_feed import CautionFeed
from drones.dispatcher import Dispatcher
from drones.flybrain_real import RealFlyBrainAdapter
from drones.pilots import FlyBrainPilot, PIDPilot
from drones.safety import SafetyLayer
from drones.sim import Drone
from ml.detectors import AnomalyDetector, RiskPredictor
from ml.features import OnlineFeatures
from shared.settings import get
from shared.config import BRAIN_WS_PORT, DATA, DRONE_COUNT, DRONE_PATROL_ALT, TELEMETRY_WS_PORT
from shared.schemas import RiskEvent, message
from shared.track import Track
from shared.telemetry import telemetry_messages

# tune in settings.toml [detection]
ANOMALY_ON = float(get("detection.anomaly_on"))
ANOMALY_TICKS = int(get("detection.anomaly_ticks"))       # 8 = ~0.5 s at 15 Hz before an incident is raised
PREDICT_TTL_S = float(get("detection.predict_ttl_s"))     # a prediction expires this long after its eta
INCIDENT_MAX_S = float(get("detection.incident_max_s"))


class Brain:
    def __init__(self, track: Track, versus: bool, fly_brain=None, steer_assist: float = 0.0):
        self.track = track
        self.features = OnlineFeatures(track)
        self.anomaly = AnomalyDetector(DATA.parent / "models" / "anomaly.pkl")
        self.risk = RiskPredictor(DATA.parent / "models" / "risk.pkl")
        for name, detector in (("incident", self.anomaly), ("risk", self.risk)):
            model = detector.model
            status = (f"trained {type(model['model']).__name__}, {len(model['features'])} features"
                      if model is not None else "rule fallback (no compatible trained model)")
            print(f"[ml] {name}: {status}", flush=True)
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
        self.slow_since = {}
        self.last_t: float | None = None
        self.clear_since = {}
        self.cooldown = {}
        self.origins = {}
        self.event_seq = 0

    def kind_of(self, f: dict) -> str:
        if abs(f["yaw_rate"]) > 1.2 and f["speed_deficit_kmh"] > 40:
            return "spin"
        if f["speed_kmh"] < 30:
            return "stopped"
        if f.get("wheels_out", 0) >= 3 or f["off_line_m"] > 15:
            return "off-track"
        return "anomaly"

    def close(self):
        for pilot in self.pilots.values():
            if isinstance(pilot, FlyBrainPilot):
                pilot.close()

    def new_event(self, t, typ, kind, car, c, severity, eta=0.0) -> dict:
        self.event_seq += 1
        ev = RiskEvent(id=f"{typ[:3]}-{car}-{int(t)}-{self.event_seq}", t=t, type=typ, kind=kind, car_ids=[car],
                       x=c["x"], y=c["y"], z=c["z"], track_pos=c["track_pos"],
                       severity=round(severity, 2), eta_s=round(eta, 1))
        self.origins[ev.id] = (c["x"], c["z"])
        return ev.__dict__

    def tick(self, t: float, cars: list[dict], yellow_controlled=None) -> list[str]:
        msgs: list[str] = []
        dt = 0.05 if self.last_t is None else min(1.0, max(0.001, t - self.last_t))
        self.last_t = t
        byid = {c["car_id"]: c for c in cars}
        for car, event in list(self.active.items()):
            if car not in byid or byid[car].get("in_pit"):
                self.end(event, msgs)
        feats = self.features.update(t, cars)

        # score every racing car of this tick in one model call
        racing = []
        for car in feats:
            c = byid[car]
            if c.get("in_pit") or t < 15:
                # a car that reaches the pits (or is removed there by AC) is no longer a track hazard
                if car in self.active and c.get("in_pit"):
                    self.end(self.active[car], msgs)
                continue
            racing.append(car)
        scored = dict(zip(racing, self.anomaly.score_many([feats[c] for c in racing])))
        intentional_slow = {car for car in (yellow_controlled or set()) if car in feats
                            and feats[car]['speed_kmh'] > 5 and feats[car].get('wheels_out',0) < 3
                            and feats[car].get('yaw_excess',abs(feats[car]['yaw_rate'])) < .7
                            and feats[car]['accel'] > -5}
        for car in intentional_slow & scored.keys():
            if scored[car][1] in (None, 'stopped', 'limp'):
                scored[car] = (0., None)
        for car in racing:
            self.hot[car] = self.hot.get(car, 0) + 1 if scored[car][0] > ANOMALY_ON else 0
        quiet = [c for c in racing if c not in self.active or self.active[c]["type"] == "predicted"]
        preds = dict(zip(quiet, self.risk.predict_many(
            [feats[c] for c in quiet], [scored[c][0] if self.hot[c] >= 3 else 0.0 for c in quiet])))
        for car in intentional_slow:
            preds.pop(car, None)

        for car in racing:
            f, c = feats[car], byid[car]
            score, model_kind = scored[car]
            act = self.active.get(car)
            # Slow running is a separate telemetry hazard: the crash classifier
            # need not label a steadily limping car as a crash.
            moving_slow = (car not in intentional_slow and float(get("detection.slow_min_kmh", 5)) < f["speed_kmh"]
                           < float(get("detection.slow_max_kmh", 100))
                           and f["speed_deficit_kmh"] > float(get("detection.slow_deficit_kmh", 60))
                           and f["accel"] > -1 and f.get("wheels_out", 0) < 3)
            if moving_slow:
                self.slow_since.setdefault(car, t)
            else:
                self.slow_since.pop(car, None)
            slow_ready = moving_slow and t - self.slow_since[car] >= float(get("detection.slow_confirm_s", 2))
            if act is None and t < self.cooldown.get(car, 0):
                continue

            if slow_ready and (act is None or act["kind"] != "limp"):
                if act and act["type"] == "predicted":
                    self.end(act, msgs)
                    act = None
                if act is None:
                    act = self.new_event(t, "incident", "limp", car, c, max(score, .6))
                    self.active[car] = act
                    self.dispatch.assign(act)
                else:
                    act["kind"] = "limp"
                self.clear_since.pop(car, None)
                self.dispatch.escort(act["id"], car)
                msgs.append(message("risk", event=act))
                msgs.append(message("report", report=telemetry_report(act, f)))

            # reactive: sustained anomaly -> incident
            if self.hot[car] >= ANOMALY_TICKS and (act is None or act["type"] == "predicted"):
                if act:
                    self.end(act, msgs)
                kind = {"off": "off-track"}.get(model_kind, model_kind) if model_kind else self.kind_of(f)
                ev = self.new_event(t, "incident", kind, car, c, score)
                self.active[car] = ev
                self.dispatch.assign(ev)
                msgs.append(message("risk", event=ev))
                msgs.append(message("report", report=telemetry_report(ev, f)))
                continue

            # predictive: fast car closing on a slow one
            if act is None:
                pred = preds.get(car)
                if pred:
                    p, eta = pred
                    kind = "at risk" if self.risk.model is not None else "closing"
                    ev = self.new_event(t, "predicted", kind, car, c, p, eta)
                    self.active[car] = ev
                    self.dispatch.assign(ev)
                    msgs.append(message("risk", event=ev))
                continue

            # lifecycle of active events
            age = t - act["t"]
            if act["type"] == "predicted":
                clear = preds.get(car) is None
                self.clear_since[car] = self.clear_since.get(car, t) if clear else t
                if (clear and t - self.clear_since[car] >= float(get("detection.clear_after_s", .75))) or age > act["eta_s"] + PREDICT_TTL_S:
                    self.end(act, msgs)
            elif act["type"] == "incident":
                origin = self.origins.get(act["id"], (act["x"], act["z"]))
                gone_m = float(np.hypot(c["x"] - origin[0], c["z"] - origin[1]))
                driving = f["speed_kmh"] > 40 and f.get("wheels_out", 0) < 3 and abs(f["yaw_rate"]) < 1.2
                clear = not moving_slow and driving and (score < ANOMALY_ON or f["speed_deficit_kmh"] < 30 or gone_m > float(get("detection.departed_m", 40)))
                if act["kind"] == "limp":
                    # Leaving the original location is expected for an escort.
                    clear = f["speed_deficit_kmh"] < 30 and f["speed_kmh"] > float(get("detection.slow_min_kmh", 5))
                    if f["speed_kmh"] <= float(get("detection.slow_min_kmh", 5)):
                        act["kind"] = "stopped"
                        for drone in self.drones:
                            if drone.event_id == act["id"]:
                                drone.mode, drone.follow_car = "hold", None
                        msgs.append(message("risk", event=act))
                        msgs.append(message("report", report=telemetry_report(act, f)))
                self.clear_since[car] = self.clear_since.get(car, t) if clear else t
                recovered = clear and t - self.clear_since[car] >= float(get("detection.clear_after_s", .75))
                if recovered or (age > INCIDENT_MAX_S and act["kind"] != "limp"):
                    self.end(act, msgs)
            if self.active.get(car) is act:
                for key in ("x", "y", "z", "track_pos"):
                    act[key] = c[key]

        # freed drones top up incidents that are still active
        for ev in {id(e): e for e in self.active.values()}.values():
            self.dispatch.reinforce(ev)
            if ev["kind"] == "limp":
                self.dispatch.escort(ev["id"], ev["car_ids"][0])
        self.slow_since = {car: since for car, since in self.slow_since.items() if car in racing}

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
        self.origins.pop(ev["id"], None)
        for car in ev["car_ids"]:
            if self.active.get(car) is ev:
                del self.active[car]
                self.hot.pop(car, None)
                self.slow.pop(car, None)
                self.slow_since.pop(car, None)
                self.clear_since.pop(car, None)
                self.cooldown[car] = (self.last_t or 0) + float(get("detection.rearm_s", 1.5))
        msgs.append(json.dumps({"type": "risk_end", "id": ev["id"]}))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-versus", action="store_true", default=not get("drones.versus"),
                    help="don't also send the fly drone to incidents (settings.toml drones.versus)")
    ap.add_argument("--steer-assist", type=float, default=float(get("flybrain.steer_assist")),
                    help="0-1, blend beacon bearing into the fly's turn (settings.toml flybrain.steer_assist)")
    args = ap.parse_args()
    fly_brain = RealFlyBrainAdapter.load()     # None -> placeholder fly
    feeds = GameFeeds()
    cautions = CautionFeed()
    track_id = None
    vision = VisionWorker()

    async def handler(ws):
        await ws.wait_closed()

    async with serve(handler, "localhost", BRAIN_WS_PORT) as server:
        print(f"brain publishing on ws://localhost:{BRAIN_WS_PORT}")
        brain, session_id = None, None

        def emit(raw):
            payload = json.loads(raw)
            payload["session_id"] = session_id
            broadcast(server.connections, json.dumps(payload))

        while True:
            try:
                async with connect(f"ws://localhost:{TELEMETRY_WS_PORT}", max_size=None) as tel:
                    print("connected to telemetry")
                    brain = None
                    cars, source_live, last_telemetry = [], False, 0.0
                    async for m in telemetry_messages(tel):
                        if m.get("type") in ("session_reset", "track"):
                            session_id = m.get("session_id")
                            feeds.reset(False)
                            vision.reset()
                            cars, last_telemetry = [], 0.0
                            emit(json.dumps({"type": "game_frames_reset"}))
                            if brain:
                                await asyncio.to_thread(brain.close)
                                brain = None
                        if m.get("type") == "track":
                            track_id = m.get("track_id")
                            source_live = m.get("source") == "live"
                            cars, last_telemetry = [], 0.0
                            feeds.reset(source_live)
                            vision.reset()
                            brain = Brain(Track(m["centerline"]), versus=not args.no_versus,
                                          fly_brain=fly_brain, steer_assist=args.steer_assist)
                            print(f"track loaded: {brain.track.length:.0f} m")
                        elif m.get("type") == "frames" and brain:
                            cars, last_telemetry = m["cars"], time.monotonic()
                            if source_live and not feeds.live:
                                feeds.reset(True)
                            controlled = cautions.controlled_cars(session_id) if source_live else set()
                            for out in await asyncio.to_thread(brain.tick, m["t"], m["cars"], controlled):
                                if '"risk' in out[:20] or '"report' in out[:20]:
                                    print(out[:160])
                                emit(out)
                        if brain and last_telemetry:
                            age = time.monotonic() - last_telemetry
                            try:
                                cautions.publish(brain, session_id, track_id, source_live, age)
                            except OSError:
                                pass  # HUD file failure must not stop detection or drone control
                            if age > 15 and feeds.live:
                                feeds.reset(False)
                                vision.reset()
                                emit(json.dumps({"type": "game_frames_reset"}))
                            # Default logger flushes every 10s. Frames remain genuinely live
                            # during that interval, but drone movement follows buffered data.
                            feeds.publish_poses(brain, cars)
                            frame_msg = feeds.poll_message([d.id for d in brain.drones])
                            if frame_msg:
                                emit(frame_msg)
                            if age < 2:
                                vision.poll(brain, feeds, emit)
            except (OSError, Exception) as e:  # telemetry not up yet, or stream ended
                feeds.reset(False)
                vision.reset()
                emit(json.dumps({"type": "game_frames_reset"}))
                if brain:
                    await asyncio.to_thread(brain.close)
                    brain = None
                print(f"waiting for telemetry ({type(e).__name__})")
                await asyncio.sleep(2)


if __name__ == "__main__":
    np.set_printoptions(precision=2, suppress=True)
    asyncio.run(main())
