"""Online feature computation. The ONLY place features are defined.

Training (ml/dataset.py) replays each session tick by tick through this same class, so
the models see exactly the features brain.py computes live: no train/serve mismatch.

The model features (FEATURES) are all relative to what is normal at that point of that
track, so a model trained on one track and car class transfers to others:
  speed as a fraction of the typical speed there, speed lost as a fraction of it,
  yaw rate beyond what the corner itself asks for (typical speed x curvature),
  acceleration beyond the typical acceleration there, gaps in seconds, closing speed
  as a fraction of typical speed. Time windows are in seconds (15 and 20 Hz logs agree).
Raw values (speed_kmh, speed_deficit_kmh, yaw_rate, accel, gaps in metres) are still in
the output for the rule baselines and the drone logic, but not given to the models.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from shared.track import Track

BASE = [
    "speed_ratio", "deficit_ratio", "accel_excess_g", "yaw_excess", "wheels_out", "off_line_m",
    "gap_ahead_s", "gap_behind_s", "closing_behind_ratio", "closing_ahead_ratio",
]
HISTORY = [
    "deficit_rise_1s", "deficit_rise_3s", "accel_excess_min_1s", "yaw_excess_max_1s", "yaw_excess_mean_1s",
    "wheels_out_max_1s", "wheels_out_frac_3s", "off_line_max_1s", "lateral_drift", "deficit_ratio_mean_3s",
    "motion_align", "ratio_min_3s",
]
FEATURES = BASE + HISTORY
HIST_S = 3.2
GAP_CAP_S = 30.0
G = 9.81


class OnlineFeatures:
    def __init__(self, track: Track):
        self.track = track
        # (t, x, z, speed, yaw_excess, wheels_out, off_line, deficit_ratio, accel_excess, ratio)
        self.hist: dict[int, deque] = defaultdict(deque)

    def update(self, t: float, cars: list[dict]) -> dict[int, dict]:
        """cars: TelemetryFrame dicts for one tick. Returns {car_id: features}."""
        out: dict[int, dict] = {}
        tr = self.track
        for c in cars:
            h = self.hist[c["car_id"]]
            if h and t < h[-1][0]:            # session restarted / time went backwards
                h.clear()
            prev = h[-1] if h else None
            i = tr.idx(c["track_pos"])
            typical = float(tr.v[i])
            v_typ = max(typical, 30.0) / 3.6                  # m/s, floored so slow corners don't blow up ratios
            speed = c["speed_kmh"]
            yaw = float(c.get("yaw_rate", 0.0))
            accel = 0.0
            if prev and t > prev[0]:
                accel = (speed - prev[3]) / 3.6 / (t - prev[0])
            accel = max(-60.0, min(30.0, accel))
            off_line = math.hypot(c["x"] - tr.x[i], c["z"] - tr.z[i])
            ratio = speed / 3.6 / v_typ
            deficit_ratio = (typical - speed) / 3.6 / v_typ
            # logger yaw is opposite in sign to the track's curvature convention (checked on real logs)
            yaw_excess = min(10.0, abs(yaw + speed / 3.6 * float(tr.curv[i])))
            accel_excess = (accel - max(-50.0, min(20.0, float(tr.acc_typ[i])))) / G
            wo = int(c.get("wheels_out", 0))
            h.append((t, c["x"], c["z"], speed, yaw_excess, wo, off_line, deficit_ratio, accel_excess, ratio))
            while h and t - h[0][0] > HIST_S:
                h.popleft()

            w1 = [e for e in h if t - e[0] <= 1.0]
            f = {
                # raw, for rules / drones / reports
                "speed_kmh": speed, "speed_deficit_kmh": typical - speed, "accel": accel,
                "yaw_rate": max(-10.0, min(10.0, yaw)), "typical_kmh": typical,
                # model features
                "speed_ratio": ratio,
                "deficit_ratio": deficit_ratio,
                "accel_excess_g": accel_excess,
                "yaw_excess": yaw_excess,
                "wheels_out": wo,
                "off_line_m": off_line,
                # how much further behind the typical speed the car has fallen recently
                # (normal braking follows the typical profile and stays ~0)
                "deficit_rise_1s": deficit_ratio - min(e[7] for e in w1),
                "deficit_rise_3s": deficit_ratio - min(e[7] for e in h),
                "accel_excess_min_1s": min(e[8] for e in w1),
                "yaw_excess_max_1s": max(e[4] for e in w1),
                "yaw_excess_mean_1s": sum(e[4] for e in w1) / len(w1),
                "wheels_out_max_1s": max(e[5] for e in w1),
                "wheels_out_frac_3s": sum(e[5] >= 2 for e in h) / len(h),
                "off_line_max_1s": max(e[6] for e in w1),
                # sideways drift away from the line, per metre travelled at typical pace
                "lateral_drift": (off_line - w1[0][6]) / max(t - w1[0][0], 0.2) / v_typ,
                "deficit_ratio_mean_3s": sum(e[7] for e in h) / len(h),
                "ratio_min_3s": min(e[9] for e in h),
            }
            # direction of travel vs the track's direction: 1 = with the track, -1 = backwards,
            # ~0 = sideways (spinning). Only meaningful when the car has moved.
            old = next((e for e in h if t - e[0] <= 0.5), None)
            dx, dz = (c["x"] - old[1], c["z"] - old[2]) if old else (0.0, 0.0)
            dist = math.hypot(dx, dz)
            f["motion_align"] = ((dx * tr.tan[i, 0] + dz * tr.tan[i, 1]) / dist) if dist > 1.0 else 0.0
            f["_v_typ"] = v_typ
            out[c["car_id"]] = f

        # gaps to physically nearest car ahead / behind along the track
        ids = [c["car_id"] for c in cars if not c.get("in_pit")]
        byid = {c["car_id"]: c for c in cars}
        lap_s = GAP_CAP_S                    # gaps beyond this don't matter; same cap on every track
        for a in out:
            out[a].update(gap_ahead_m=tr.length, gap_behind_m=tr.length, car_ahead=None, car_behind=None,
                          closing_behind_mps=0.0, closing_ahead_mps=0.0,
                          gap_ahead_s=lap_s, gap_behind_s=lap_s, closing_behind_ratio=0.0, closing_ahead_ratio=0.0)
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
            v_typ = f.pop("_v_typ")
            if best_a[1] is not None:
                f["gap_ahead_m"] = best_a[0] * tr.length
                f["car_ahead"] = best_a[1]
                f["closing_ahead_mps"] = (byid[a]["speed_kmh"] - byid[best_a[1]]["speed_kmh"]) / 3.6
                f["gap_ahead_s"] = min(lap_s, f["gap_ahead_m"] / v_typ)
                f["closing_ahead_ratio"] = f["closing_ahead_mps"] / v_typ
            if best_b[1] is not None:
                f["gap_behind_m"] = best_b[0] * tr.length
                f["car_behind"] = best_b[1]
                f["closing_behind_mps"] = (byid[best_b[1]]["speed_kmh"] - byid[a]["speed_kmh"]) / 3.6
                f["gap_behind_s"] = min(lap_s, f["gap_behind_m"] / v_typ)
                f["closing_behind_ratio"] = f["closing_behind_mps"] / v_typ
        for f in out.values():
            f.pop("_v_typ", None)
        return out
