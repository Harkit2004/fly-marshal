"""Session folder -> training table with features and labels.

Adds per car per tick:
  accel (m/s^2), progress (laps), gap_ahead_m, gap_behind_m, closing_behind_mps,
  speed_deficit_kmh (typical speed at this track_pos minus speed), off_line_m,
  incident_now   1 while a ground-truth event is active for this car
  incident_in_5s 1 if an incident starts for this car within the next 5 s (training label)

yaw_rate, wheels_out and G-forces come straight from the logger, so they are not derived here.
Ground truth comes from events.csv (logger analyzer episodes + contacts, or synthetic events),
with a small rule fallback for sessions without events.

Usage:
  python -m pipeline.process data/sessions/spa_example
  python -m pipeline.process data/sessions/*            (several at once)
Output: data/processed/<session>.parquet (CSV if pyarrow is missing)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from shared.config import DATA

HORIZON_S = 5.0
EVENT_WINDOW_S = 3.0       # an event marks the car as "in incident" for this long
LABEL_KINDS = {"spin", "slide", "stuck", "off", "off_oversteer", "off_understeer", "contact",
               "stopped", "rejoin", "dnf"}


def add_features(df: pd.DataFrame, cl: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["car_id", "t"]).reset_index(drop=True)
    g = df.groupby("car_id", group_keys=False)
    dt = g.t.diff().replace(0, np.nan)
    df["accel"] = (g.speed_kmh.diff() / 3.6 / dt).fillna(0).clip(-60, 30)

    bins = len(cl)
    idx = np.minimum((df.track_pos.to_numpy() * bins).astype(int), bins - 1)
    df["speed_deficit_kmh"] = cl.typical_speed_kmh.to_numpy()[idx] - df.speed_kmh
    df["off_line_m"] = np.hypot(df.x - cl.x.to_numpy()[idx], df.z - cl.z.to_numpy()[idx])
    length = float(np.hypot(np.diff(cl.x, append=cl.x.iloc[0]), np.diff(cl.z, append=cl.z.iloc[0])).sum())
    df["progress"] = df.lap + df.track_pos

    # gaps along the track to the physically nearest car ahead / behind, per tick
    tp = df.pivot(index="t", columns="car_id", values="track_pos")
    sp = df.pivot(index="t", columns="car_id", values="speed_kmh") / 3.6
    pit = df.pivot(index="t", columns="car_id", values="in_pit").fillna(1).to_numpy() > 0
    a = tp.to_numpy()
    n = a.shape[1]
    diff = (a[:, None, :] - a[:, :, None]) % 1.0          # [T, i, j] = how far j is ahead of i
    diff[:, np.arange(n), np.arange(n)] = np.inf
    diff = np.where(np.isnan(diff) | pit[:, None, :], np.inf, diff)
    j_ahead = np.argmin(diff, axis=2)
    gap_ahead = np.take_along_axis(diff, j_ahead[..., None], 2)[..., 0] * length
    back = np.full_like(diff, np.inf)
    finite = np.isfinite(diff)
    back[finite] = (1.0 - diff[finite]) % 1.0
    j_behind = np.argmin(back, axis=2)
    gap_behind = np.take_along_axis(back, j_behind[..., None], 2)[..., 0] * length
    s = sp.to_numpy()
    closing_behind = np.take_along_axis(s, j_behind, 1) - s

    def melt(arr, name):
        return pd.DataFrame(arr, index=tp.index, columns=tp.columns).stack(future_stack=True).rename(name)

    extra = pd.concat([melt(gap_ahead, "gap_ahead_m"), melt(gap_behind, "gap_behind_m"),
                       melt(closing_behind, "closing_behind_mps")], axis=1)
    extra.index.names = ["t", "car_id"]
    df = df.merge(extra.reset_index(), on=["t", "car_id"], how="left")
    df[["gap_ahead_m", "gap_behind_m"]] = df[["gap_ahead_m", "gap_behind_m"]].clip(upper=length)
    return df.replace([np.inf, -np.inf], np.nan)


def add_labels(df: pd.DataFrame, events: pd.DataFrame | None) -> pd.DataFrame:
    df = df.sort_values(["car_id", "t"]).reset_index(drop=True)
    inc = np.zeros(len(df), dtype=bool)
    starts = []
    if events is not None and len(events):
        ev = events[events.kind.isin(LABEL_KINDS)]
        for e in ev.itertuples():
            inc |= ((df.car_id == e.car_id) & df.t.between(e.t, e.t + EVENT_WINDOW_S)).to_numpy()
            starts.append((e.car_id, e.t))
    else:  # rule fallback when a session has no ground truth
        racing = df.in_pit == 0
        stopped = (df.speed_kmh < 30) & (df.speed_deficit_kmh > 80)
        spin = (df.yaw_rate.abs() > 1.5) & (df.speed_kmh > 30)
        off = df.wheels_out >= 3
        inc = (racing & (stopped | spin | off) & (df.t > 20)).to_numpy()
    df["incident_now"] = inc.astype(int)

    # incident_in_5s: an incident starts for this car in (t, t + 5]
    if not starts:  # derive starts from rising edges of incident_now
        edge = df.incident_now.diff().fillna(0) > 0
        starts = list(zip(df.car_id[edge], df.t[edge]))
    label = np.zeros(len(df), dtype=int)
    t = df.t.to_numpy()
    car = df.car_id.to_numpy()
    for c, t0 in starts:
        label |= ((car == c) & (t < t0) & (t >= t0 - HORIZON_S)).astype(int)
    df["incident_in_5s"] = label
    return df


def process(session: Path) -> pd.DataFrame:
    df = pd.read_csv(session / "telemetry.csv")
    cl = pd.read_csv(session / "centerline.csv")
    ev_path = session / "events.csv"
    ev = pd.read_csv(ev_path) if ev_path.exists() else None
    df = add_features(df, cl)
    df = add_labels(df, ev)
    df.insert(0, "session", session.name)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="+", type=Path, help="session folder(s) under data/sessions")
    ap.add_argument("--out-dir", type=Path, default=DATA / "processed")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for session in args.sessions:
        if not (session / "telemetry.csv").exists():
            continue
        df = process(session)
        out = args.out_dir / (session.name + ".parquet")
        try:
            df.to_parquet(out, index=False)
        except ImportError:
            out = out.with_suffix(".csv")
            df.to_csv(out, index=False)
        print(f"{session.name}: {len(df):,} rows -> {out.name}   "
              f"incident_now {df.incident_now.sum():,}   incident_in_5s {df.incident_in_5s.sum():,}")


if __name__ == "__main__":
    main()
