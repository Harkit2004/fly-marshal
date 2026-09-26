"""Session folders -> training tables, using the live feature code (ml/features.py).

Every session is replayed tick by tick through OnlineFeatures, exactly like brain.py does,
and each (tick, car) row gets:
  cls       what is happening NOW: none / spin / off / slide / stopped / contact
            (-1 = ambiguous, left out of detector training and scoring)
  y_soon    1 if an incident starts for this car within the next HORIZON_S seconds
            (only on rows where the car is not already in an incident; else -1)
Ground truth is events.csv: the race logger's analyzer episodes, contacts and the
adapter's stopped/limp rules.

  python -m ml.dataset data/sessions/vl_*            -> data/processed/<session>.feat.parquet
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from ml.features import FEATURES, OnlineFeatures
from shared.config import DATA
from shared.schemas import FRAME_FIELDS
from shared.track import Track

CLASSES = ["none", "spin", "off", "slide", "stopped", "contact"]
KIND_TO_CLASS = {
    "spin": "spin", "off": "off", "off_oversteer": "off", "off_understeer": "off",
    "slide": "slide", "stopped": "stopped", "stuck": "stopped", "dnf": "stopped", "contact": "contact",
}
WINDOW_S = {"spin": 3.0, "off": 3.0, "slide": 2.0, "stopped": 3.0, "contact": 1.0}
CONTACT_MIN_SEVERITY = 0.15     # relative speed / 100 km/h; lighter touches aren't incidents
PRE_IGNORE_S = 0.5              # just before a labelled start is ambiguous for the detector
HORIZON_S = 5.0
START_S = 15.0                  # grid launch looks anomalous; brain.py ignores it too
OUT = DATA / "processed"


def incidents(events: pd.DataFrame) -> pd.DataFrame:
    ev = events[events.kind.isin(KIND_TO_CLASS)].copy()
    ev["cls"] = ev.kind.map(KIND_TO_CLASS)
    light = (ev.cls == "contact") & (ev.severity < CONTACT_MIN_SEVERITY)
    return ev[~light].sort_values("t"), ev[light]


def build(session: Path) -> pd.DataFrame:
    tel = pd.read_csv(session / "telemetry.csv")
    cl = pd.read_csv(session / "centerline.csv")
    track = Track(cl[["track_pos", "x", "y", "z", "typical_speed_kmh"]].values.tolist())
    fe = OnlineFeatures(track)
    rows = []
    for t, g in tel.groupby("t", sort=True):
        cars = g[FRAME_FIELDS].to_dict("records")
        feats = fe.update(float(t), cars)
        if t < START_S:
            continue
        for c in cars:
            if c["in_pit"]:
                continue
            f = feats[c["car_id"]]
            rows.append([float(t), c["car_id"], c["speed_kmh"]] + [f[k] for k in FEATURES])
    df = pd.DataFrame(rows, columns=["t", "car_id", "speed_kmh"] + FEATURES)   # raw speed: for labels only

    ev, light = incidents(pd.read_csv(session / "events.csv"))
    t, car = df.t.to_numpy(), df.car_id.to_numpy()
    cls = np.zeros(len(df), dtype=int)
    ignore = np.zeros(len(df), dtype=bool)
    starts: dict[int, list[float]] = {}
    for e in ev.itertuples():
        mine = car == e.car_id
        k = CLASSES.index(e.cls)
        end = e.t + WINDOW_S[e.cls]
        if e.cls == "stopped":           # stays "stopped" until the car moves again (max 30 s)
            later = df[(car == e.car_id) & (t > e.t) & (df.speed_kmh > 30)]
            end = min(e.t + 30.0, later.t.min()) if len(later) else e.t + 30.0
            end = max(end, e.t + WINDOW_S["stopped"])
        cls[mine & (t >= e.t) & (t <= end)] = k
        ignore |= mine & (t >= e.t - PRE_IGNORE_S) & (t < e.t)
        starts.setdefault(e.car_id, []).append(e.t)
    for e in light.itertuples():        # light touches: neither incident nor clean
        ignore |= (car == e.car_id) & (np.abs(t - e.t) <= 1.0)
    cls = np.where(ignore & (cls == 0), -1, cls)

    y = np.zeros(len(df), dtype=int)
    for c, ts in starts.items():
        mine = car == c
        for s in ts:
            y |= (mine & (t < s) & (t >= s - HORIZON_S)).astype(int)
    y = np.where(cls != 0, -1, y)        # already in (or ambiguous) -> not a prediction row
    df["cls"], df["y_soon"] = cls, y
    df.insert(0, "session", session.name)
    return df


def build_and_save(session: Path) -> str:
    df = build(session)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT / f"{session.name}.feat.parquet", index=False)
    counts = df.cls.value_counts().to_dict()
    return (f"{session.name}: {len(df):,} rows  "
            + "  ".join(f"{CLASSES[k] if k >= 0 else 'ignore'}={v:,}" for k, v in sorted(counts.items()))
            + f"  soon={int((df.y_soon == 1).sum()):,}")


def load(names: list[str]) -> pd.DataFrame:
    return pd.concat([pd.read_parquet(OUT / f"{n}.feat.parquet") for n in names], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="+", type=Path)
    args = ap.parse_args()
    with ProcessPoolExecutor(max_workers=min(8, len(args.sessions))) as ex:
        for line in ex.map(build_and_save, args.sessions):
            print(line)


if __name__ == "__main__":
    main()
