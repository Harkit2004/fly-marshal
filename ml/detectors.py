"""Incident (reactive) and risk (predictive) detectors.

With trained models (python -m ml.train -> models/*.pkl; LightGBM, logistic regression or a
blend of both, whichever cross-validated best):
  AnomalyDetector   multiclass none/spin/off/slide/stopped/contact. Score = 1 - P(none),
                    rescaled so the model's chosen threshold sits at 0.5 (settings.toml
                    detection.anomaly_on keeps meaning "the default alarm level").
  RiskPredictor     P(an incident starts for this car within 5 s).
Without model files they fall back to the rule baselines below, so the pipeline always runs.
Both score all cars of a tick in one call (score_many / predict_many).
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np


class Blend:
    """Average of several fitted classifiers' probabilities (same classes)."""

    def __init__(self, models):
        self.models = models
        self.classes_ = models[0].classes_

    def predict_proba(self, X):
        return sum(m.predict_proba(X) for m in self.models) / len(self.models)


def _load(path: Path | None):
    if path and path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    return None


def _matrix(model: dict, feats: list[dict]) -> np.ndarray:
    # missing / NaN -> 0, exactly as in training (ml/train.py clean_X)
    return np.nan_to_num(np.array([[float(f.get(k, 0.0) or 0.0) for k in model["features"]] for f in feats]),
                         nan=0.0, posinf=0.0, neginf=0.0)


def _rescale(p: np.ndarray, thr: float) -> np.ndarray:
    """Map probability so that `thr` -> 0.5, monotonically."""
    return np.where(p < thr, 0.5 * p / thr, 0.5 + 0.5 * (p - thr) / max(1 - thr, 1e-6))


class AnomalyDetector:
    """How much does this car look like it's in an incident right now, and what kind."""

    def __init__(self, model_path: Path | None = None):
        self.model = _load(model_path)
        if self.model and self.model.get("type") != "multiclass":
            self.model = None                       # old format: use the rules

    def score_many(self, feats: list[dict]) -> list[tuple[float, str | None]]:
        if not feats:
            return []
        if self.model is not None:
            p = self.model["model"].predict_proba(_matrix(self.model, feats))
            inc = _rescale(1 - p[:, 0], self.model["threshold"])
            kinds = [self.model["classes"][1 + int(np.argmax(r[1:]))] for r in p]
            return list(zip(inc.tolist(), kinds))
        return [(self.rule_score(f), None) for f in feats]

    def score(self, f: dict) -> float:
        return self.score_many([f])[0][0]

    @staticmethod
    def rule_score(f: dict) -> float:
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
    """Predicts an incident before it happens."""

    def __init__(self, model_path: Path | None = None):
        self.model = _load(model_path)
        if self.model and self.model.get("type") != "binary":
            self.model = None

    def predict_many(self, feats: list[dict], anomalies: list[float]) -> list[tuple[float, float] | None]:
        if not feats:
            return []
        if self.model is not None:
            p = self.model["model"].predict_proba(_matrix(self.model, feats))[:, 1]
            out = []
            for f, pi in zip(feats, p.tolist()):
                if pi <= self.model["threshold"]:
                    out.append(None)
                    continue
                closing = f.get("closing_behind_mps", 0.0)
                eta = f.get("gap_behind_m", 1e9) / closing if closing > 1 else self.model["horizon_s"] / 2
                out.append((pi, min(eta, self.model["horizon_s"])))
            return out
        return [self.rule_predict(f, a) for f, a in zip(feats, anomalies)]

    def predict(self, car_id: int, f: dict, anomaly: float):
        return self.predict_many([f], [anomaly])[0]

    @staticmethod
    def rule_predict(f: dict, anomaly: float):
        # baseline: a slow/stopped car with a much faster car closing from behind
        slow = f["speed_deficit_kmh"] > 100 or anomaly > 0.5
        closing = f.get("closing_behind_mps", 0.0)
        gap = f.get("gap_behind_m", 1e9)
        if slow and closing > 15 and gap < 600:
            eta = gap / closing
            if eta < 10:
                return min(1.0, 0.5 + closing / 80), eta
        return None
