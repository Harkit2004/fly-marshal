"""Online feature computation, matching the column names in pipeline/process.py.

Models trained offline on data/processed/*.parquet must see the same features
live, so any feature added to process.py should be added here too.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from shared.track import Track

FEATURES = [
    "speed_kmh", "speed_deficit_kmh", "accel", "yaw_rate", "wheels_out", "off_line_m",
    "gap_ahead_m", "gap_behind_m", "closing_behind_mps",
]


class OnlineFeatures:
    def __init__(self, track: Track, history: int = 40):
        self.track = track
        self.hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=history))

    def update(self, t: float, cars: list[dict]) -> dict[int, dict]:
        """cars: TelemetryFrame dicts for one tick. Returns {car_id: features}."""
        out: dict[int, dict] = {}
        tr = self.track
        for c in cars:
            h = self.hist[c["car_id"]]
            prev = h[-1] if h else None
            h.append((t, c["x"], c["z"], c["speed_kmh"]))
            yaw_rate = accel = 0.0
            if "yaw_rate" in c:                       # logged by the VRC Race Logger
                yaw_rate = c["yaw_rate"]
            elif prev and len(h) >= 7:
                # heading over 3-sample chords (0.15 s) to keep position jitter out of yaw rate
                (t0, x0, z0, _), (t1, x1, z1, _) = h[-7], h[-4]
                dt = t - t1
                if dt > 0 and (c["x"] - x1) ** 2 + (c["z"] - z1) ** 2 > 0.25 and (x1 - x0) ** 2 + (z1 - z0) ** 2 > 0.25:
                    a0 = math.atan2(z1 - z0, x1 - x0)
                    a1 = math.atan2(c["z"] - z1, c["x"] - x1)
                    yaw_rate = ((a1 - a0 + math.pi) % (2 * math.pi) - math.pi) / dt
            if prev and t > prev[0]:
                accel = (c["speed_kmh"] - prev[3]) / 3.6 / (t - prev[0])
            i = tr.idx(c["track_pos"])
            out[c["car_id"]] = {
                "speed_kmh": c["speed_kmh"],
                "speed_deficit_kmh": float(tr.v[i]) - c["speed_kmh"],
                "accel": max(-60.0, min(30.0, accel)),
                "yaw_rate": max(-10.0, min(10.0, yaw_rate)),
                "wheels_out": int(c.get("wheels_out", 0)),
                "off_line_m": math.hypot(c["x"] - tr.x[i], c["z"] - tr.z[i]),
            }

        # gaps to physically nearest car ahead / behind along the track
        ids = [c["car_id"] for c in cars if not c.get("in_pit")]
        byid = {c["car_id"]: c for c in cars}
        for a in ids:
            best_a = best_b = (math.inf, None)
            for b in ids:
                if a == b:
                    continue
                d = (byid[b]["track_pos"] - byid[a]["track_pos"]) % 1.0
                if d < best_a[0]:
                    best_a = (d, b)
                if 1.0 - d < best_b[0]:
                    best_b = (1.0 - d, b)
            f = out[a]
            f["gap_ahead_m"] = best_a[0] * tr.length
            f["gap_behind_m"] = best_b[0] * tr.length
            f["car_ahead"] = best_a[1]
            f["car_behind"] = best_b[1]
            f["closing_behind_mps"] = ((byid[best_b[1]]["speed_kmh"] - byid[a]["speed_kmh"]) / 3.6
                                       if best_b[1] is not None else 0.0)
        return out
