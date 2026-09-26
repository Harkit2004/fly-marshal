"""Build the dashboard/brain "track" message for a live race, with no converted session needed.

Two sources (settings.toml live.track_source):
  ai_line    the track's own AI line, <AC>/content/tracks/<track>/<layout>/ai/fast_lane.ai,
             read with the race logger's analyzer. Exact shape, per-point widths and the AI's
             target speed (used as "typical speed" until real laps exist).
  first_lap  learnt from the cars' own positions: every sample is binned by its spline
             position (0-1 around the lap); once ~all bins have data the centreline is the
             per-bin mean position and the typical speed the per-bin median speed.
Custom tracks without an AI line work with first_lap: drive one clean lap and the track appears.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from shared.config import CENTERLINE_BINS, ROOT
from shared.settings import dashboard_settings

sys.path.insert(0, str(ROOT / "third_party" / "assetto-corsa-race-logger" / "analyzer"))
import track_model as vrc_track  # noqa: E402

FIRST_LAP_COVERAGE = 0.97      # share of bins that need samples before the track is built


def _message(track_id: str, x, y, z, speed_kmh, wl=None, wr=None) -> str:
    s = np.arange(len(x)) / len(x)
    seg = np.hypot(np.diff(x, append=x[0]), np.diff(z, append=z[0])).sum()
    msg = {
        "type": "track",
        "track_id": track_id,
        "length_m": float(seg),
        "centerline": [[round(float(a), 5), *map(float, b)] for a, b in
                       zip(s, np.round(np.stack([x, y, z, speed_kmh], axis=1), 3))],
        "settings": dashboard_settings(),
    }
    if wl is not None:
        msg["widths"] = np.round(np.stack([wl, wr], axis=1), 2).tolist()
    return json.dumps(msg)


def ai_line_path(track_full: str, ac_root: str | None) -> str | None:
    return vrc_track.resolve_ai_path({"trackFull": track_full}, ac_root)


def from_ai_line(path: str, track_id: str, samples: list[tuple[float, float, float]],
                 bins: int = CENTERLINE_BINS) -> str:
    """samples: a few live (spline, x, z) points, used to pick the file's z sign convention."""
    tm = vrc_track.load_fast_lane(path)
    if samples:
        sp, sx, sz = (np.array(a) for a in zip(*samples))
        d_as_is = np.median(tm.dist_to_line(sx, sz, sp))
        tm.flip_z()
        if d_as_is <= np.median(tm.dist_to_line(sx, sz, sp)):
            tm.flip_z()
    s = np.arange(bins) / bins
    x, y, z = (np.interp(s, tm.s, tm.pts[:, k]) for k in range(3))
    v = np.interp(s, tm.s, np.resize(tm.speed, len(tm.s)))
    v = v * 3.6 if np.nanmax(v) < 150 else v          # AI line speeds are stored in m/s
    wl = np.interp(s, tm.s, np.resize(tm.side_l, len(tm.s)))
    wr = np.interp(s, tm.s, np.resize(tm.side_r, len(tm.s)))
    return _message(track_id, x, y, z, v, wl, wr)


class FirstLap:
    """Accumulates live samples until the whole lap is covered."""

    def __init__(self, bins: int = CENTERLINE_BINS):
        self.bins = bins
        self.cnt = np.zeros(bins)
        self.sum = np.zeros((bins, 3))
        self.speeds: list[list[float]] = [[] for _ in range(bins)]

    def add(self, car: dict) -> None:
        if car["speed_kmh"] < 60 or car["wheels_out"] > 0 or car.get("in_pit"):
            return
        b = min(int(car["track_pos"] * self.bins), self.bins - 1)
        self.cnt[b] += 1
        self.sum[b] += (car["x"], car["y"], car["z"])
        if len(self.speeds[b]) < 50:
            self.speeds[b].append(car["speed_kmh"])

    @property
    def coverage(self) -> float:
        return float((self.cnt > 0).mean())

    def message(self, track_id: str) -> str:
        good = self.cnt > 0
        idx = np.arange(self.bins)
        pts = np.zeros((self.bins, 3))
        pts[good] = self.sum[good] / self.cnt[good, None]
        for k in range(3):
            pts[:, k] = np.interp(idx, idx[good], pts[good, k], period=self.bins)
        v = np.array([np.median(a) if a else np.nan for a in self.speeds])
        v = np.interp(idx, idx[~np.isnan(v)], v[~np.isnan(v)], period=self.bins)
        v = np.convolve(np.pad(v, 7, mode="wrap"), np.ones(15) / 15, "valid")   # smooth over ~50 m
        return _message(track_id, pts[:, 0], pts[:, 1], pts[:, 2], v)
