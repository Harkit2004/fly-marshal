"""Run the brain offline over a session (no websockets) and score it against events.csv.

Reports, per ground-truth incident (one per car per episode):
  detected      the brain raised an incident for that car within [t0 - 1 s, t0 + 8 s]
  latency       seconds from ground-truth start to the brain's incident
  warned        a "predicted" event for that car came first, and how early
plus false alarms (brain incidents with no ground truth within 10 s) and
drone arrival time (first drone within 30 m of the incident's hold point).

Hard car-to-car contacts (relative speed >= 15 km/h) are scored as their own incidents.
--rules ignores models/*.pkl and scores the rule baseline, for a like-for-like comparison.
--json writes the summary for tools/compare_models.py.

Usage: python tools/evaluate.py data/sessions/spa_example [--start 15] [--rules] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brain import Brain  # noqa: E402
from shared.schemas import FRAME_FIELDS  # noqa: E402
from shared.track import Track  # noqa: E402

GT_KINDS = {"spin", "slide", "stuck", "off", "off_oversteer", "off_understeer", "stopped", "dnf"}


def run(session: Path, start: float, rules: bool = False):
    df = pd.read_csv(session / "telemetry.csv")
    cl = pd.read_csv(session / "centerline.csv")
    brain = Brain(Track(cl[["track_pos", "x", "y", "z", "typical_speed_kmh"]].values.tolist()), versus=True)
    if rules:
        brain.anomaly.model = brain.risk.model = None
    raised, arrivals = [], {}
    for t, g in df[df.t >= start].groupby("t"):
        for m in brain.tick(float(t), g[FRAME_FIELDS].to_dict("records")):
            d = json.loads(m)
            if d["type"] == "risk" and d["event"]["id"] not in {r["id"] for r in raised}:
                raised.append(d["event"])
            elif d["type"] == "drones":
                for dr in d["drones"]:
                    eid = dr.get("event_id")
                    if eid and dr["target"] and eid not in arrivals.get(dr["pilot"], {}):
                        if np.hypot(dr["x"] - dr["target"][0], dr["z"] - dr["target"][2]) < 30:
                            arrivals.setdefault(dr["pilot"], {})[eid] = float(t)
    return raised, arrivals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path)
    ap.add_argument("--start", type=float, default=15.0, help="skip the start (grid launch looks anomalous)")
    ap.add_argument("--rules", action="store_true", help="rule baseline (ignore trained models)")
    ap.add_argument("--json", type=Path, help="write a summary here")
    args = ap.parse_args()

    raised, arrivals = run(args.session, args.start, args.rules)
    ev = pd.read_csv(args.session / "events.csv")
    gt = ev[ev.kind.isin(GT_KINDS) & (ev.t >= args.start)]
    gt = gt.sort_values("t").groupby(["car_id", "episode"], as_index=False).first()
    hard = ev[(ev.kind == "contact") & (ev.severity >= 0.15) & (ev.t >= args.start)].copy()
    hard = hard[~hard.apply(lambda h: ((gt.car_id == h.car_id) & (gt.t.sub(h.t).abs() < 3)).any(), axis=1)]         if len(gt) else hard                  # a contact that is part of a spin/off is the same incident
    gt = pd.concat([gt, hard], ignore_index=True).sort_values("t")

    incidents = [r for r in raised if r["type"] == "incident"]
    predicted = [r for r in raised if r["type"] == "predicted"]
    rows = []
    for g in gt.itertuples():
        hit = [r for r in incidents if g.car_id in r["car_ids"] and g.t - 1 <= r["t"] <= g.t + 8]
        warn = [r for r in predicted if g.car_id in r["car_ids"] and g.t - 10 <= r["t"] < g.t]
        r0 = hit[0] if hit else None
        rows.append({
            "t": round(g.t, 1), "car": g.car_id, "kind": g.kind,
            "detected": bool(hit), "latency_s": round(r0["t"] - g.t, 2) if r0 else None,
            "warned_s_before": round(g.t - warn[0]["t"], 1) if warn else None,
            "pid_arrival_s": round(arrivals.get("pid", {}).get(r0["id"], np.nan) - r0["t"], 1) if r0 else None,
            "fly_arrival_s": round(arrivals.get("fly", {}).get(r0["id"], np.nan) - r0["t"], 1) if r0 else None,
        })
    res = pd.DataFrame(rows)
    false_alarms = [r for r in incidents
                    if not ((ev.car_id == r["car_ids"][0]) & (ev.t.between(r["t"] - 10, r["t"] + 10))).any()]

    pd.set_option("display.width", 140)
    print(res.to_string(index=False))
    n = len(res)
    print(f"\nground-truth incidents: {n}   detected: {res.detected.sum()} ({res.detected.mean():.0%})"
          if n else "\nno ground-truth incidents in range")
    if n and res.detected.any():
        print(f"median detection latency: {res.latency_s.median():.2f} s   "
              f"warned in advance: {res.warned_s_before.notna().sum()}")
    minutes = (pd.read_csv(args.session / "telemetry.csv", usecols=["t"]).t.max() - args.start) / 60
    print(f"false alarms: {len(false_alarms)} ({len(false_alarms) / max(minutes, 1e-9):.1f} per minute)   "
          f"predicted warnings raised: {len(predicted)}")
    if n:
        res["group"] = res.kind.map(lambda k: "contact" if k == "contact" else
                                    "off" if k.startswith("off") else "stopped" if k in ("stuck", "dnf") else k)
        print("by kind: " + "   ".join(f"{k} {int(g.detected.sum())}/{len(g)}" for k, g in res.groupby("group")))
    if args.json:
        useful = [r for r in predicted if ev[(ev.car_id == r["car_ids"][0]) & ev.t.between(r["t"], r["t"] + 10)
                                             & (ev.kind != "contact") | (ev.car_id == r["car_ids"][0])
                                             & ev.t.between(r["t"], r["t"] + 10) & (ev.severity >= 0.15)].shape[0]]
        args.json.write_text(json.dumps({
            "session": args.session.name, "mode": "rules" if args.rules else "model", "incidents": n,
            "detected": int(res.detected.sum()) if n else 0,
            "median_latency_s": float(res.latency_s.median()) if n and res.detected.any() else None,
            "warned": int(res.warned_s_before.notna().sum()) if n else 0,
            "median_warning_s": float(res.warned_s_before.median()) if n and res.warned_s_before.notna().any() else None,
            "false_alarms": len(false_alarms), "minutes": round(minutes, 2),
            "predicted_warnings": len(predicted), "predicted_followed_by_incident": len(useful),
            "by_kind": {k: [int(g.detected.sum()), len(g)] for k, g in res.groupby("group")} if n else {},
        }, indent=2))


if __name__ == "__main__":
    main()
