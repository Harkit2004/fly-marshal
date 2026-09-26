"""Incident (reactive) and risk (predictive) detectors.

Both are rule-based baselines so the full pipeline runs from day one.
Replace the internals with trained models without changing the interface:

  AnomalyDetector.score(features) -> 0..1
      TODO(ML): IsolationForest trained on clean laps only (no labels needed).
  RiskPredictor.predict(car_id, features, cars) -> (probability, eta_s) or None
      TODO(ML): LightGBM on incident_in_5s from data/processed, split by session.
"""

from __future__ import annotations

import pickle
from pathlib import Path


class AnomalyDetector:
    """Scores how un-normal a car's driving looks right now."""

    def __init__(self, model_path: Path | None = None):
        self.model = None
        if model_path and model_path.exists():
            with open(model_path, "rb") as f:
                self.model = pickle.load(f)      # e.g. sklearn IsolationForest + feature list

    def score(self, f: dict) -> float:
        if self.model is not None:
            feats = [f[k] for k in self.model["features"]]
            raw = -self.model["model"].score_samples([feats])[0]         # higher = more anomalous
            return max(0.0, min(1.0, (raw - self.model["lo"]) / (self.model["hi"] - self.model["lo"])))
        # baseline rules
        s = 0.0
        if f["speed_deficit_kmh"] > 80 and f["speed_kmh"] < 30:
            s = max(s, 0.9)                       # stopped where others are fast
        if abs(f["yaw_rate"]) > 1.2 and f["speed_deficit_kmh"] > 40 and f["speed_kmh"] > 30:
            s = max(s, 0.8)                       # rotating fast while losing speed = spin
        if f.get("wheels_out", 0) >= 3 or f["off_line_m"] > 15:
            s = max(s, 0.6)                       # off track
        # No braking-G rule: F1 cars brake at ~5 G every corner, so it fires constantly.
        return s


class RiskPredictor:
    """Predicts a dangerous encounter before it happens."""

    def __init__(self, model_path: Path | None = None):
        self.model = None
        if model_path and model_path.exists():
            with open(model_path, "rb") as f:
                self.model = pickle.load(f)

    def predict(self, car_id: int, f: dict, anomaly: float) -> tuple[float, float] | None:
        if self.model is not None:
            feats = [f[k] if k != "anomaly" else anomaly for k in self.model["features"]]
            p = float(self.model["model"].predict_proba([feats])[0, 1])
            return (p, 5.0) if p > self.model.get("threshold", 0.5) else None
        # baseline: a slow/stopped car with a much faster car closing from behind
        slow = f["speed_deficit_kmh"] > 100 or anomaly > 0.5
        closing = f.get("closing_behind_mps", 0.0)
        gap = f.get("gap_behind_m", 1e9)
        if slow and closing > 15 and gap < 600:
            eta = gap / closing
            if eta < 10:
                p = min(1.0, 0.5 + closing / 80)
                return p, eta
        return None
