"""Generate a fake race in the exact logger format, so ML, drones and the
dashboard can be built before real Assetto Corsa data exists.

Outputs a session folder, same layout as pipeline/vrclog_adapter.py:
  data/sessions/synthetic_00/{telemetry.csv, events.csv, centerline.csv, meta.json}

Scripted incidents (ground truth in events.csv):
  t=70   car 5 spins, stops on the racing line for 25 s, then limps at 60 km/h
  t=150  car 9 runs wide off track, crawls, then rejoins into traffic

Usage: python tools/make_synthetic.py [--cars 14] [--seconds 240] [--seed 7]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import CENTERLINE_BINS, DATA, SAMPLE_HZ  # noqa: E402
from shared.schemas import EVENT_COLUMNS, TELEMETRY_COLUMNS  # noqa: E402

A_LAT = 25.0      # m/s^2 cornering limit
A_ACC = 6.0
A_BRAKE = 14.0
V_MAX = 265 / 3.6


def build_track(bins: int):
    th = np.linspace(0, 2 * np.pi, 20000, endpoint=False)
    r = 500 * (1 + 0.25 * np.sin(3 * th) + 0.1 * np.cos(5 * th))
    x, z = r * np.cos(th), 0.7 * r * np.sin(th)
    seg = np.hypot(np.diff(x, append=x[0]), np.diff(z, append=z[0]))
    s = np.concatenate([[0], np.cumsum(seg)[:-1]])
    length = seg.sum()
    su = np.linspace(0, length, bins, endpoint=False)
    xs, zs = np.interp(su, s, x, period=length), np.interp(su, s, z, period=length)
    tp = su / length
    ys = 5 * np.sin(2 * np.pi * tp) + 3 * np.sin(6 * np.pi * tp)

    # curvature -> speed limit, then accel/brake passes (twice for wraparound)
    dx, dz = np.gradient(xs), np.gradient(zs)
    ddx, ddz = np.gradient(dx), np.gradient(dz)
    k = np.abs(dx * ddz - dz * ddx) / np.power(dx * dx + dz * dz, 1.5)
    k = np.convolve(np.r_[k[-10:], k, k[:10]], np.ones(21) / 21, "valid")
    v = np.minimum(V_MAX, np.sqrt(A_LAT / np.maximum(k, 1e-6)))
    ds = length / bins
    for _ in range(2):
        for i in range(bins):
            v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * A_ACC * ds))
        for i in range(bins - 1, -1, -1):
            v[i] = min(v[i], np.sqrt(v[(i + 1) % bins] ** 2 + 2 * A_BRAKE * ds))

    heading = np.arctan2(dz, dx)
    normal = np.stack([-np.sin(heading), np.cos(heading)], axis=1)
    return dict(length=length, tp=tp, x=xs, y=ys, z=zs, v=v, normal=normal, heading=heading)


def lookup(track, s):
    """Interpolate centreline arrays at arc length s (m, any lap)."""
    b = len(track["tp"])
    f = (s % track["length"]) / track["length"] * b
    i0 = np.floor(f).astype(int) % b
    i1 = (i0 + 1) % b
    w = f - np.floor(f)
    out = {}
    for key in ("x", "y", "z", "v"):
        out[key] = track[key][i0] * (1 - w) + track[key][i1] * w
    out["normal"] = track["normal"][i0]
    out["heading"] = track["heading"][i0]
    return out


def simulate(track, n_cars: int, seconds: float, seed: int):
    rng = np.random.default_rng(seed)
    dt = 1.0 / SAMPLE_HZ
    L = track["length"]
    skill = rng.uniform(0.955, 1.0, n_cars)
    s = -np.arange(n_cars) * 35.0 + 5.0          # grid, car 0 on pole
    speed = np.zeros(n_cars)
    offset = np.zeros(n_cars)
    offset_target = rng.normal(0, 1.0, n_cars)
    lap_start = np.zeros(n_cars)
    last_lap = np.floor(s / L)
    spin_angle = np.zeros(n_cars)
    last_head = np.zeros(n_cars)
    rows = []

    SPIN_CAR, SPIN_T = 5, 70.0
    WIDE_CAR, WIDE_T = 9, 150.0

    steps = int(seconds * SAMPLE_HZ)
    for step in range(steps):
        t = step * dt
        c = lookup(track, s)
        target = c["v"] * skill

        # scripted incidents
        mode = ["race"] * n_cars
        if n_cars > SPIN_CAR:
            ts = t - SPIN_T
            if 0 <= ts < 2.5:
                mode[SPIN_CAR] = "spin"
            elif 2.5 <= ts < 27.5:
                mode[SPIN_CAR] = "stopped"
            elif ts >= 27.5:
                mode[SPIN_CAR] = "limp"
        if n_cars > WIDE_CAR:
            tw = t - WIDE_T
            if 0 <= tw < 1.5:
                mode[WIDE_CAR] = "wide"
            elif 1.5 <= tw < 6:
                mode[WIDE_CAR] = "crawl"
            elif 6 <= tw < 10:
                mode[WIDE_CAR] = "rejoin"

        # obstacle avoidance: cars move over and lift near a stopped/slow car
        slow = [i for i in range(n_cars) if mode[i] in ("stopped", "crawl", "limp")]
        order = np.argsort(s)
        for i in range(n_cars):
            if mode[i] != "race":
                continue
            for j in slow:
                gap = s[j] - s[i]
                if 0 < gap < 150:
                    target[i] *= 0.8
                    offset_target[i] = -5.0 if offset[j] >= 0 else 5.0
            # don't drive through the car ahead
            ahead = [j for j in order if 0 < s[j] - s[i] < 12 and mode[j] == "race"]
            for j in ahead:
                target[i] = min(target[i], speed[j])

        for i in range(n_cars):
            m = mode[i]
            if m == "spin":
                target[i] = 0.0
                offset_target[i] = 3.0
                spin_angle[i] += 3 * np.pi / 2.5 * dt
            elif m == "stopped":
                target[i] = 0.0
            elif m == "limp":
                target[i] = min(target[i], 60 / 3.6)
                offset_target[i] = 4.5
                spin_angle[i] = 0.0
            elif m == "wide":
                target[i] = min(target[i], 30.0)
                offset_target[i] = 11.0
            elif m == "crawl":
                target[i] = 50 / 3.6
                offset_target[i] = 10.0
            elif m == "rejoin":
                offset_target[i] = 0.0
            elif abs(offset_target[i]) > 3 and rng.random() < 0.01:
                offset_target[i] = rng.normal(0, 1.0)

        # longitudinal dynamics
        brake_rate = np.where(np.array(mode) == "spin", 25.0, A_BRAKE)
        dv = np.clip(target - speed, -brake_rate * dt, A_ACC * dt)
        accel = dv / dt
        speed = np.maximum(0.0, speed + dv)
        s = s + speed * dt
        offset += np.clip(offset_target - offset, -3 * dt, 3 * dt) + rng.normal(0, 0.02, n_cars)

        c = lookup(track, s)
        px = c["x"] + c["normal"][:, 0] * offset
        pz = c["z"] + c["normal"][:, 1] * offset
        head = c["heading"] + spin_angle
        vx, vz = speed * np.cos(head), speed * np.sin(head)
        lap = np.floor(s / L)
        crossed = lap > last_lap
        lap_start[crossed] = t
        last_lap = lap
        ranks = np.empty(n_cars, int)
        ranks[np.argsort(-s)] = np.arange(1, n_cars + 1)

        yaw_rate = (head - last_head + np.pi) % (2 * np.pi) - np.pi
        yaw_rate = yaw_rate / dt if step else np.zeros(n_cars)
        last_head = head
        for i in range(n_cars):
            kmh = speed[i] * 3.6
            rows.append([
                round(t, 3), i, f"AI Driver {i + 1}", "synthetic_gt3", "",
                px[i], c["y"][i], pz[i], kmh, np.degrees((head[i] + np.pi) % (2 * np.pi) - np.pi), yaw_rate[i],
                0.0, 0.0, accel[i] / 9.81,
                1.0 if accel[i] > 0.5 else 0.0, min(1.0, max(0.0, -accel[i] / A_BRAKE)), 0.0,
                int(min(6, 1 + kmh // 45)), 4 if abs(offset[i]) > 7 else 0, 0,
                (s[i] % L) / L, max(0, int(lap[i])), int(ranks[i]), 0,
            ])

    events = [
        [SPIN_T, SPIN_CAR, "spin", -1, 0.9, "synthetic", 0],
        [SPIN_T + 2.5, SPIN_CAR, "stopped", -1, 0.9, "synthetic", 0],
        [SPIN_T + 27.5, SPIN_CAR, "limp", -1, 0.5, "synthetic", 0],
        [WIDE_T, WIDE_CAR, "off", -1, 0.6, "synthetic", 1],
        [WIDE_T + 6, WIDE_CAR, "rejoin", -1, 0.6, "synthetic", 1],
    ]
    events = [e for e in events if e[1] < n_cars and e[0] < seconds]
    return rows, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cars", type=int, default=14)
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=DATA / "sessions" / "synthetic_00")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    track = build_track(CENTERLINE_BINS)
    rows, events = simulate(track, args.cars, args.seconds, args.seed)

    df = pd.DataFrame(rows, columns=TELEMETRY_COLUMNS)
    df.to_csv(args.out / "telemetry.csv", index=False, float_format="%.4f")
    pd.DataFrame(events, columns=EVENT_COLUMNS).to_csv(args.out / "events.csv", index=False)
    pd.DataFrame({
        "track_pos": track["tp"], "x": track["x"], "y": track["y"], "z": track["z"],
        "typical_speed_kmh": track["v"] * 3.6 * 0.975,
    }).to_csv(args.out / "centerline.csv", index=False, float_format="%.4f")
    meta = {
        "source": "synthetic", "track": "synthetic_oval", "track_length_m": round(track["length"], 1),
        "session": "race", "sample_hz": SAMPLE_HZ, "duration_s": args.seconds,
        "cars": [{"car_id": i, "driver": f"AI Driver {i + 1}", "car_model": "synthetic_gt3", "ai": True}
                 for i in range(args.cars)],
        "notes": "Car 5 spins and stops at t=70, limps from t=97.5. Car 9 goes wide at t=150, rejoins at 156.",
    }
    (args.out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {len(df):,} rows, track {track['length']:.0f} m -> {args.out}")


if __name__ == "__main__":
    main()
