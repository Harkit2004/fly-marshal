"""Train the incident detector and the incident predictor.

  detector   multiclass, per (tick, car): none / spin / off / slide / stopped / contact
             brain.py uses 1 - P(none) as the incident score and the argmax as the kind
  predictor  binary, per (tick, car): will an incident start for this car within 5 s?

Protocol (nothing from the test sessions is used for any choice):
  1. model selection   compare LightGBM, sklearn HistGradientBoosting, RandomForest and
                       LogisticRegression with leave-one-session-out CV on the train sessions
  2. tuning            random search over LightGBM parameters, same CV
  3. thresholds        chosen on the out-of-fold predictions with the same alarm logic as
                       brain.py (score sustained for N ticks), for a false-alarm budget
  4. final fit         on all train sessions -> models/anomaly.pkl, models/risk.pkl
  5. test              scored once on the held-out sessions (unseen track / car class)
Results go to reports/training/.

  python -m ml.train --train vl_clean vl_spin vl_offs vl_stopped vl_contact vl_rejoin_limp \
                     --test spa_gt3_demo spa_example
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.dataset import CLASSES, load
from ml.detectors import Blend
from ml.features import FEATURES
from shared.config import ROOT

warnings.filterwarnings("ignore", category=UserWarning)
MODELS = ROOT / "models"
REPORTS = ROOT / "reports" / "training"
RNG = np.random.default_rng(0)            # only for drawing tuning candidates
NEG_KEEP = 0.25            # keep 1 in 4 "nothing happening" rows for training (weighted back up)
FA_BUDGET_PER_MIN = 0.3    # detector threshold: best recall with at most this many false alarms/min
SUSTAIN_TICKS = 8          # must match settings.toml detection.anomaly_ticks


# ---------------------------------------------------------------- data
def subsample(df: pd.DataFrame, neg_mask: np.ndarray, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    """Same seed -> same rows, so every candidate model is compared on identical data."""
    keep = ~neg_mask | (np.random.default_rng(seed).random(len(df)) < NEG_KEEP)
    w = np.where(neg_mask, 1 / NEG_KEEP, 1.0)[keep]
    return df[keep], w


def det_rows(df):
    return df[df.cls >= 0]


def pred_rows(df):
    return df[df.y_soon >= 0]


# ---------------------------------------------------------------- models
def make(kind: str, task: str, params: dict | None = None):
    n_cls = len(CLASSES)
    if kind == "lightgbm":
        p = dict(n_estimators=400, learning_rate=0.05, num_leaves=31, min_child_samples=40,
                 subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                 class_weight="balanced", n_jobs=-1, verbose=-1, random_state=0)
        p.update(params or {})
        return lgb.LGBMClassifier(objective="multiclass" if task == "det" else "binary",
                                  **({"num_class": n_cls} if task == "det" else {}), **p)
    if kind == "hist_gb":
        return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, class_weight="balanced",
                                              random_state=0)
    if kind == "random_forest":
        return RandomForestClassifier(n_estimators=200, max_depth=14, min_samples_leaf=20,
                                      class_weight="balanced_subsample", n_jobs=-1, random_state=0)
    if kind == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=400, class_weight="balanced"))
    raise ValueError(kind)


def fit(model, X, y, w):
    last = model.steps[-1][0] + "__sample_weight" if hasattr(model, "steps") else "sample_weight"
    model.fit(X, y, **{last: w})
    return model


def proba(model, X, task):
    p = model.predict_proba(X)
    if task == "det":                      # align columns to CLASSES even if a class was absent
        full = np.zeros((len(X), len(CLASSES)))
        full[:, model.classes_] = p
        return full
    return p[:, 1]


def clean_X(df):
    return df[FEATURES].astype(float).fillna(0.0).to_numpy()


# ---------------------------------------------------------------- CV
def loso(df: pd.DataFrame, kind: str, task: str, params=None) -> np.ndarray:
    """Out-of-fold predictions, one fold per training session."""
    rows = det_rows(df) if task == "det" else pred_rows(df)
    y_all = rows.cls.to_numpy() if task == "det" else rows.y_soon.to_numpy()
    oof = np.zeros((len(rows), len(CLASSES))) if task == "det" else np.zeros(len(rows))
    for fold, s in enumerate(sorted(rows.session.unique())):
        tr, te = (rows.session != s).to_numpy(), (rows.session == s).to_numpy()
        sub, w = subsample(rows[tr], y_all[tr] == 0, seed=fold)
        y = sub.cls.to_numpy() if task == "det" else sub.y_soon.to_numpy()
        m = fit(make(kind, task, params), clean_X(sub), y, w)
        oof[te] = proba(m, clean_X(rows[te]), task)
    return oof


def det_scores(y, p) -> dict:
    inc = 1 - p[:, 0]
    out = {"ap_incident": average_precision_score(y > 0, inc), "auc_incident": roc_auc_score(y > 0, inc)}
    for k, c in enumerate(CLASSES[1:], start=1):
        if (y == k).sum() >= 5:
            out[f"ap_{c}"] = average_precision_score(y == k, p[:, k])
    out["kind_accuracy"] = float((p[y > 0].argmax(1) == y[y > 0]).mean())
    return out


def pred_scores(y, p) -> dict:
    return {"ap_soon": average_precision_score(y, p), "auc_soon": roc_auc_score(y, p)}


# ---------------------------------------------------------------- alarm-level evaluation
def alarms(rows: pd.DataFrame, score: np.ndarray, thr: float, sustain: int, hold_s: float = 10.0):
    """brain.py logic: an alarm when the score stays above thr for `sustain` ticks in a row; at most
    one alarm per car per `hold_s`. Returns list of (session, car, t). Vectorised."""
    key = rows.session.astype(str).to_numpy() + "#" + rows.car_id.astype(str).to_numpy()
    order = np.lexsort((rows.t.to_numpy(), key))
    k, t, above = key[order], rows.t.to_numpy()[order], (score > thr)[order]
    new_grp = np.r_[True, k[1:] != k[:-1]]
    prev_above = np.r_[False, above[:-1]] & ~new_grp
    starts = above & ~prev_above                 # first tick of a run above the threshold
    idx = np.arange(len(k))
    run_start = np.maximum.accumulate(np.where(starts, idx, 0))
    count = np.where(above, idx - run_start + 1, 0)
    hit = np.flatnonzero(count == sustain)
    out, last = [], {}
    for i in hit:
        if t[i] - last.get(k[i], -1e9) > hold_s:
            s_, c_ = k[i].split("#")
            out.append((s_, int(c_), float(t[i])))
            last[k[i]] = t[i]
    return out


def event_metrics(rows: pd.DataFrame, events: pd.DataFrame, al: list, minutes: float,
                  before=1.0, after=8.0) -> dict:
    """Detected = alarm for that car within [start - before, start + after]. Mirrors tools/evaluate.py."""
    a = pd.DataFrame(al, columns=["session", "car_id", "t"])
    hits, lat = 0, []
    for e in events.itertuples():
        m = a[(a.session == e.session) & (a.car_id == e.car_id) & a.t.between(e.t - before, e.t + after)]
        if len(m):
            hits += 1
            lat.append(m.t.min() - e.t)
    fa = 0
    for x in a.itertuples():
        near = events[(events.session == x.session) & (events.car_id == x.car_id)
                      & events.t.between(x.t - 10, x.t + 10)]
        fa += near.empty
    return {"events": len(events), "detected": hits, "recall": hits / max(len(events), 1),
            "median_latency_s": float(np.median(lat)) if lat else None,
            "false_alarms": fa, "false_alarms_per_min": fa / max(minutes, 1e-9)}


def gt_events(names: list[str], soon=False) -> pd.DataFrame:
    """One ground-truth incident per car per episode (first phase), like tools/evaluate.py,
    plus hard contacts (as their own events)."""
    from ml.dataset import CONTACT_MIN_SEVERITY, KIND_TO_CLASS, START_S
    out = []
    for n in names:
        ev = pd.read_csv(ROOT / "data" / "sessions" / n / "events.csv")
        ev = ev[ev.kind.isin(KIND_TO_CLASS) & (ev.t >= START_S)]
        ev = ev[(ev.kind != "contact") | (ev.severity >= CONTACT_MIN_SEVERITY)]
        ep = ev[ev.episode >= 0].sort_values("t").groupby(["car_id", "episode"], as_index=False).first()
        other = ev[ev.episode < 0]
        # merge events of one car closer than 3 s (a spin + stopped + contact is one incident)
        e = pd.concat([ep, other]).sort_values(["car_id", "t"])
        e = e[e.groupby("car_id").t.diff().fillna(99) > 3.0]
        e["session"] = n
        out.append(e[["session", "car_id", "t", "kind"]])
    return pd.concat(out, ignore_index=True)


def minutes_of(rows):
    return sum((g.t.max() - g.t.min()) / 60 for _, g in rows.groupby("session"))


def pick_threshold(rows, score, events, minutes, sustain, budget):
    best = None
    for thr in np.round(np.linspace(0.3, 0.97, 28), 3):
        m = event_metrics(rows, events, alarms(rows, score, thr, sustain), minutes)
        m["threshold"] = float(thr)
        if m["false_alarms_per_min"] <= budget and (best is None or m["recall"] > best["recall"]
                                                    or (m["recall"] == best["recall"] and m["false_alarms"] < best["false_alarms"])):
            best = m
    return best


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--test", nargs="*", default=[])
    ap.add_argument("--trials", type=int, default=10, help="LightGBM random-search trials")
    ap.add_argument("--candidates", nargs="+", default=["lightgbm", "hist_gb", "random_forest", "logistic"],
                    help="model types to compare (logistic is needed for the blend)")
    args = ap.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(exist_ok=True)
    report: dict = {"train": args.train, "test": args.test, "features": FEATURES, "classes": CLASSES}
    t0 = time.time()
    df = load(args.train)
    print(f"train rows {len(df):,} from {len(args.train)} sessions")

    # 1. model selection
    cands = args.candidates
    prev = json.loads((REPORTS / "report.json").read_text()) if (REPORTS / "report.json").exists() else {}
    comp, oofs = {}, {}
    for kind in cands:
        t1 = time.time()
        oofs[(kind, "det")], oofs[(kind, "pred")] = loso(df, kind, "det"), loso(df, kind, "pred")
        d = det_scores(det_rows(df).cls.to_numpy(), oofs[(kind, "det")])
        p = pred_scores(pred_rows(df).y_soon.to_numpy(), oofs[(kind, "pred")])
        comp[kind] = {**{k: round(v, 4) for k, v in {**d, **p}.items()}, "seconds": round(time.time() - t1, 1)}
        print(f"  {kind:14s} det AP {d['ap_incident']:.3f}  kind acc {d['kind_accuracy']:.3f}   "
              f"pred AP {p['ap_soon']:.3f}   ({time.time() - t1:.0f} s)")
    report["model_comparison_loso"] = comp

    # 2. tune LightGBM (the fastest to run live and usually the best on tabular data)
    space = dict(num_leaves=[15, 31, 63], min_child_samples=[20, 40, 100], learning_rate=[0.03, 0.05, 0.1],
                 n_estimators=[200, 400, 700], colsample_bytree=[0.6, 0.8, 1.0], reg_lambda=[0.0, 1.0, 5.0])
    best = {}
    for task, score_fn, key, rows_fn, ycol in [("det", det_scores, "ap_incident", det_rows, "cls"),
                                               ("pred", pred_scores, "ap_soon", pred_rows, "y_soon")]:
        seeded = [t["params"] for t in prev.get(f"tuning_{task}", [])[:2]]     # best of the last run first
        trials = [{}] + seeded + [{k: v[RNG.integers(len(v))] for k, v in space.items()} for _ in range(args.trials)]
        res = []
        for i, prm in enumerate(trials):
            oof = loso(df, "lightgbm", task, prm)
            s = score_fn(rows_fn(df)[ycol].to_numpy(), oof)[key]
            res.append((s, prm, oof))
            print(f"  tune {task} {i:2d}/{len(trials) - 1}  AP {s:.4f}  {prm}")
        res.sort(key=lambda r: -r[0])
        best[task] = ("lightgbm",) + res[0]
        # the tuned LightGBM vs logistic regression vs a 50/50 blend of both, on the same folds
        y_t = rows_fn(df)[ycol].to_numpy()
        if ("logistic", task) in oofs:
            lo = oofs[("logistic", task)]
            for name, oof in [("logistic", lo), ("blend", (res[0][2] + lo) / 2)]:
                sc = score_fn(y_t, oof)[key]
                print(f"  {task}: {name} AP {sc:.4f} vs tuned lightgbm {res[0][0]:.4f}")
                if sc > best[task][1]:
                    best[task] = (name, sc, res[0][1], oof)
        print(f"  -> {task} model: {best[task][0]} (CV AP {best[task][1]:.4f})")
        report[f"tuning_{task}"] = [{"ap": round(s, 4), "params": {k: (float(v) if isinstance(v, float) else int(v))
                                                                    for k, v in p.items()}} for s, p, _ in res[:5]]

    # 3. thresholds on out-of-fold predictions, alarm-level
    rows = det_rows(df)
    ev_train = gt_events(args.train)
    inc_oof = 1 - best["det"][3][:, 0]
    mins = minutes_of(rows)
    det_thr = pick_threshold(rows, inc_oof, ev_train, mins, SUSTAIN_TICKS, FA_BUDGET_PER_MIN)
    print(f"detector threshold {det_thr['threshold']}: CV recall {det_thr['recall']:.2%}, "
          f"{det_thr['false_alarms_per_min']:.2f} false alarms/min, latency {det_thr['median_latency_s']}")
    prow = pred_rows(df)
    p_oof = best["pred"][3]
    # predictor: warnings (score sustained 3 ticks) that come 0.5-5 s before an incident of that car
    pred_thr = None
    for thr in np.round(np.linspace(0.2, 0.95, 16), 3):
        al = alarms(prow, p_oof, thr, 3, hold_s=10.0)
        a = pd.DataFrame(al, columns=["session", "car_id", "t"])
        early, lead = 0, []
        for e in ev_train.itertuples():
            w = a[(a.session == e.session) & (a.car_id == e.car_id) & a.t.between(e.t - 5.0, e.t - 0.3)]
            if len(w):
                early += 1
                lead.append(e.t - w.t.min())
        useless = sum(ev_train[(ev_train.session == x.session) & (ev_train.car_id == x.car_id)
                               & ev_train.t.between(x.t, x.t + 6)].empty for x in a.itertuples())
        cand = {"threshold": float(thr), "warned": early, "events": len(ev_train),
                "warned_rate": early / len(ev_train), "median_lead_s": float(np.median(lead)) if lead else None,
                "false_warnings_per_min": useless / mins}
        if cand["false_warnings_per_min"] <= 0.5 and (pred_thr is None or cand["warned"] > pred_thr["warned"]):
            pred_thr = cand
    if pred_thr is None:
        pred_thr = {"threshold": 0.95, "warned": 0, "events": len(ev_train), "warned_rate": 0.0,
                    "median_lead_s": None, "false_warnings_per_min": None}
    print(f"predictor threshold {pred_thr['threshold']}: CV warned {pred_thr['warned']}/{pred_thr['events']} "
          f"(lead {pred_thr['median_lead_s']} s), {pred_thr['false_warnings_per_min']} false warnings/min")
    report["cv_alarm_level"] = {"detector": det_thr, "predictor": pred_thr}

    # 4. final fit on all train sessions
    finals, lgbms = {}, {}
    for task, rows_fn, ycol in [("det", det_rows, "cls"), ("pred", pred_rows, "y_soon")]:
        r = rows_fn(df)
        sub, w = subsample(r, r[ycol].to_numpy() == 0, seed=99)
        X, y = clean_X(sub), sub[ycol].to_numpy()
        choice = best[task][0]
        lg = fit(make("lightgbm", task, best[task][2]), X, y, w) if choice != "logistic" else None
        lo = fit(make("logistic", task), X, y, w) if choice != "lightgbm" else None
        finals[task] = lg if choice == "lightgbm" else lo if choice == "logistic" else Blend([lg, lo])
        lgbms[task] = lg
    report["chosen"] = {t: best[t][0] for t in best}
    if lgbms["det"] is not None:
        imp = pd.Series(lgbms["det"].booster_.feature_importance("gain"), index=FEATURES).sort_values(ascending=False)
        report["feature_importance_detector"] = {k: round(float(v), 1) for k, v in imp.items()}
    with open(MODELS / "anomaly.pkl", "wb") as f:
        pickle.dump({"type": "multiclass", "model": finals["det"], "kind": best["det"][0], "features": FEATURES,
                     "classes": CLASSES, "threshold": det_thr["threshold"], "trained_on": args.train}, f)
    with open(MODELS / "risk.pkl", "wb") as f:
        pickle.dump({"type": "binary", "model": finals["pred"], "kind": best["pred"][0], "features": FEATURES,
                     "threshold": pred_thr["threshold"], "horizon_s": 5.0, "trained_on": args.train}, f)
    print(f"saved models/anomaly.pkl and models/risk.pkl")

    # 5. held-out test (row level here; end-to-end in the brain with tools/evaluate.py)
    if args.test:
        te = load(args.test)
        out = {}
        for n in args.test:
            t = te[te.session == n]
            r = det_rows(t)
            p = proba(finals["det"], clean_X(r), "det")
            d = det_scores(r.cls.to_numpy(), p)
            ev = gt_events([n])
            m = event_metrics(r, ev, alarms(r, 1 - p[:, 0], det_thr["threshold"], SUSTAIN_TICKS), minutes_of(r))
            pr = pred_rows(t)
            ps = pred_scores(pr.y_soon.to_numpy(), proba(finals["pred"], clean_X(pr), "pred"))
            out[n] = {**{k: round(v, 4) for k, v in {**d, **ps}.items()}, "alarm_level": m}
            print(f"  TEST {n}: det AP {d['ap_incident']:.3f}  kind acc {d['kind_accuracy']:.3f}  "
                  f"pred AP {ps['ap_soon']:.3f}  | recall {m['recall']:.0%} ({m['detected']}/{m['events']}), "
                  f"{m['false_alarms_per_min']:.2f} FA/min, latency {m['median_latency_s']}")
        report["test"] = out
    report["seconds"] = round(time.time() - t0, 1)
    (REPORTS / "report.json").write_text(json.dumps(report, indent=2, default=float))
    print(f"report -> {REPORTS / 'report.json'}  ({report['seconds']} s)")


if __name__ == "__main__":
    main()
