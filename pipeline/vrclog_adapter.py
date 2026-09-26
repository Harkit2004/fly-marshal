"""Convert a VRC Race Logger session (third_party/assetto-corsa-race-logger) into
our session folder format, reusing that project's parser, track model and
incident detectors instead of re-implementing them.

  vrclog_*.txt  ->  data/sessions/<name>/
                      telemetry.csv    one row per car per 15 Hz tick (shared.schemas.TELEMETRY_COLUMNS)
                      events.csv       ground truth: analyzer episodes (spin/slide/off/stuck/dnf), car-to-car
                                       contacts, retirements, and rule-based stopped / limping cars
                      centerline.csv   track_pos, x, y, z, typical_speed_kmh
                      meta.json

Centreline: the track's AI line (fast_lane.ai) from your AC install when it can be
found (set AC_ROOT or pass --ac-root), otherwise averaged from the cars' own positions.

Usage:
  python -m pipeline.vrclog_adapter data/raw/vrclog_20260803_231630_spa-layout_f1_2025_race_r13.txt
  python -m pipeline.vrclog_adapter <log.txt> --name spa_chaos_01 --ac-root "D:/Steam/steamapps/common/assettocorsa"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from shared.config import CENTERLINE_BINS, DATA, ROOT
from shared.schemas import EVENT_COLUMNS, TELEMETRY_COLUMNS

ANALYZER = ROOT / "third_party" / "assetto-corsa-race-logger" / "analyzer"
sys.path.insert(0, str(ANALYZER))
import detectors as vrc_detectors  # noqa: E402
import track_model as vrc_track  # noqa: E402
import vrclog_parser  # noqa: E402

PIT_FLAGS = 1 | 2          # S-stream flags: inPitlane | inPitBox


def hold_last(t_src: np.ndarray, v_src: np.ndarray, t_dst: np.ndarray, default=0):
    """Sample a slow stream (1 Hz) onto the fast grid, holding the last value."""
    if len(t_src) == 0:
        return np.full(len(t_dst), default)
    i = np.searchsorted(t_src, t_dst, side="right") - 1
    out = v_src[np.clip(i, 0, len(v_src) - 1)]
    return np.where(i >= 0, out, default)


def track_from_positions(rd, bins: int = CENTERLINE_BINS):
    """Build a vrc TrackModel from the cars' own positions when fast_lane.ai isn't available."""
    xs, ys, zs, ss = [], [], [], []
    for ci in range(rd.n_cars):
        f = rd.F[ci]
        ok = (f["speed"] > 60) & (f["out"] == 0)
        xs.append(f["x"][ok]); ys.append(f["y"][ok]); zs.append(f["z"][ok]); ss.append(f["spline"][ok])
    x, y, z, s = (np.concatenate(a).astype(np.float64) for a in (xs, ys, zs, ss))
    b = np.minimum((s * bins).astype(int), bins - 1)
    cnt = np.bincount(b, minlength=bins)
    pts = np.stack([np.bincount(b, w, minlength=bins) for w in (x, y, z)], axis=1)
    good = cnt > 0
    pts[good] /= cnt[good, None]
    if (~good).any():
        idx = np.arange(bins)
        for k in range(3):
            pts[:, k] = np.interp(idx, idx[good], pts[good, k], period=bins)
    pts = np.vstack([pts, pts[:1]])              # closed loop, like fast_lane.ai
    tm = vrc_track.TrackModel(pts, np.zeros(len(pts)), np.zeros((len(pts), 18)))
    segs = tm.detect_segments()
    tm.corners = [{"n": str(i + 1), "name": "", "s0": c["s0"], "s1": c["s1"], "dir": c["dir"]}
                  for i, c in enumerate(segs)]
    tm.straights = []
    return tm


def load_track(rd, ac_root: str | None):
    ai = vrc_track.resolve_ai_path(rd.meta, ac_root)
    if ai:
        tm = vrc_track.load_fast_lane(ai)
        tm, med = vrc_track.pick_z_sign(tm, rd)
        segs = tm.detect_segments()
        tm.corners = [{"n": str(i + 1), "name": "", "s0": c["s0"], "s1": c["s1"], "dir": c["dir"]}
                      for i, c in enumerate(segs)]
        tm.straights = []
        return tm, f"fast_lane.ai ({ai}), on-line check {med:.2f} m"
    return track_from_positions(rd), "averaged from car positions (fast_lane.ai not found)"


def telemetry_frame(rd) -> pd.DataFrame:
    parts = []
    for ci in range(rd.n_cars):
        f, s = rd.F[ci], rd.S[ci]
        t = f["t"].astype(np.float64)
        if len(t) == 0:
            continue
        flags = hold_last(s["t"], s["flags"], t)
        parts.append(pd.DataFrame({
            "t": np.round(t, 3), "car_id": ci,
            "driver": rd.cars[ci].get("driver", f"car{ci}"), "car_model": rd.cars[ci].get("car", ""),
            "x": f["x"], "y": f["y"], "z": f["z"], "speed_kmh": f["speed"],
            "heading_deg": f["compass"], "yaw_rate": f["yaw_rate"],
            "acc_x": f["acc_x"], "acc_y": f["acc_y"], "acc_z": f["acc_z"],
            "gas": f["gas"], "brake": f["brake"], "steer": f["steer"], "gear": f["gear"],
            "wheels_out": f["out"], "surf": f["surf"], "track_pos": f["spline"],
            "lap": hold_last(s["t"], s["lap"], t), "position": hold_last(s["t"], s["race_pos"], t),
            "in_pit": ((flags & PIT_FLAGS) > 0).astype(int),
        }))
    df = pd.concat(parts, ignore_index=True).sort_values(["t", "car_id"])
    return df[TELEMETRY_COLUMNS]


def events_frame(rd, an) -> pd.DataFrame:
    rows = []
    for k, e in enumerate(an.episodes):
        for p in e["phases"]:
            rows.append([round(p["t0"], 3), p["car"], p["kind"], -1,
                         round(float(e.get("severity", 0)), 3), "detector", k])
    coll = rd.ev_coll
    for i in range(len(coll["t"])):
        if coll["other"][i] >= 0:     # car-to-car contact (raw 0 = track / floor scrape)
            rows.append([round(float(coll["t"][i]), 3), int(coll["car"][i]), "contact", int(coll["other"][i]),
                         round(float(coll["rel_speed"][i]) / 100, 3), "coll", -1])
    for ev in rd.events:
        if ev["type"] == "RETIRE":
            rows.append([round(ev["t"], 3), ev["car"], "retire", -1, 1.0, "logger", -1])
    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    return df.sort_values("t").reset_index(drop=True)


def rule_events(tel: pd.DataFrame, cl: pd.DataFrame, min_stop_s: float = 2.0, min_limp_s: float = 8.0) -> pd.DataFrame:
    """Hazards the analyzer doesn't call loss of control: a car stopped on track, or limping.
    Stopped = under 15 km/h, not in the pits, for min_stop_s. Limping = 30-110 km/h while the
    typical speed there is 80+ km/h higher, for min_limp_s. The first 30 s (grid, launch) is skipped."""
    bins = len(cl)
    typ = cl.typical_speed_kmh.to_numpy()
    rows = []
    for car, g in tel.groupby("car_id"):
        g = g.sort_values("t")
        t = g.t.to_numpy()
        deficit = typ[np.minimum((g.track_pos.to_numpy() * bins).astype(int), bins - 1)] - g.speed_kmh.to_numpy()
        racing = (g.in_pit.to_numpy() == 0) & (t > 30)
        for kind, mask, min_s in [
            ("stopped", racing & (g.speed_kmh.to_numpy() < 15), min_stop_s),
            ("limp", racing & (g.speed_kmh.to_numpy() > 30) & (g.speed_kmh.to_numpy() < 110) & (deficit > 80), min_limp_s),
        ]:
            edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
            for a, b in zip(edges[::2], edges[1::2]):
                if t[b - 1] - t[a] >= min_s:
                    rows.append([round(float(t[a]), 3), int(car), kind, -1, round(float(t[b - 1] - t[a]), 1), "rule", -1])
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def centerline_frame(tm, df: pd.DataFrame, bins: int = CENTERLINE_BINS) -> pd.DataFrame:
    s = np.arange(bins) / bins
    pts = tm.pts
    x = np.interp(s, tm.s, pts[:, 0])
    y = np.interp(s, tm.s, pts[:, 1])
    z = np.interp(s, tm.s, pts[:, 2])
    clean = df[(df.in_pit == 0) & (df.wheels_out == 0) & (df.speed_kmh > 40)]
    b = np.minimum((clean.track_pos * bins).astype(int), bins - 1)
    v = clean.groupby(b).speed_kmh.median().reindex(range(bins)).interpolate(limit_direction="both")
    # half widths from the AI line payload when it has them (fast_lane.ai), else the model's constant
    wl = np.interp(s, tm.s, np.resize(tm.side_l, len(tm.s)))
    wr = np.interp(s, tm.s, np.resize(tm.side_r, len(tm.s)))
    return pd.DataFrame({"track_pos": s, "x": x, "y": y, "z": z, "typical_speed_kmh": v.to_numpy(),
                         "half_width_l": wl, "half_width_r": wr})


def convert(log: Path, name: str | None, ac_root: str | None) -> Path:
    rd = vrclog_parser.parse(str(log))
    tm, source = load_track(rd, ac_root)
    an = vrc_detectors.analyze(rd, tm)
    out = DATA / "sessions" / (name or log.stem.replace("vrclog_", ""))
    out.mkdir(parents=True, exist_ok=True)

    tel = telemetry_frame(rd)
    tel.to_csv(out / "telemetry.csv", index=False, float_format="%.4f")
    cl = centerline_frame(tm, tel)
    cl.to_csv(out / "centerline.csv", index=False, float_format="%.4f")
    ev = pd.concat([events_frame(rd, an), rule_events(tel, cl)], ignore_index=True).sort_values("t")
    ev.to_csv(out / "events.csv", index=False)
    meta = {
        "source": "vrc_race_logger", "log": log.name, "app_version": rd.app_version,
        "track": rd.meta.get("trackFull"), "track_length_m": round(tm.total, 1),
        "logged_track_length_m": rd.meta.get("trackLengthM"), "session": rd.meta.get("sessionName"),
        "sample_hz": rd.meta.get("fastHz"), "duration_s": round(rd.duration, 1),
        "centerline_source": source,
        "cars": [{"car_id": i, "driver": c.get("driver"), "car_model": c.get("car"), "ai": c.get("ai")}
                 for i, c in enumerate(rd.cars)],
        "counts": {"episodes": len(an.episodes), "contacts": len(an.contacts), "dnfs": len(an.dnfs),
                   "event_rows": len(ev)},
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"{log.name}: {rd.n_cars} cars, {rd.duration / 60:.1f} min, {len(tel):,} rows")
    print(f"  centreline: {source}")
    print(f"  {len(an.episodes)} incident episodes, {len(an.contacts)} contacts -> {len(ev)} event rows")
    print(f"  -> {out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path, help="vrclog_*.txt (or a .parts dir)")
    ap.add_argument("--name", help="session folder name (default: from the log filename)")
    ap.add_argument("--ac-root", default=os.environ.get("AC_ROOT"), help="AC install, for fast_lane.ai")
    args = ap.parse_args()
    convert(args.log, args.name, args.ac_root)


if __name__ == "__main__":
    main()
