"""Track geometry from the centreline, used online by ML and drones."""

from __future__ import annotations

import numpy as np


class Track:
    def __init__(self, centerline: list[list[float]]):
        a = np.asarray(centerline, dtype=float)       # track_pos, x, y, z, typical_speed_kmh
        self.tp, self.x, self.y, self.z, self.v = a.T
        self.bins = len(a)
        self.xyz = a[:, 1:4]
        seg = np.hypot(np.diff(self.x, append=self.x[0]), np.diff(self.z, append=self.z[0]))
        self.length = float(seg.sum())
        tx = np.roll(self.x, -1) - np.roll(self.x, 1)
        tz = np.roll(self.z, -1) - np.roll(self.z, 1)
        n = np.hypot(tx, tz) + 1e-9
        self.tan = np.stack([tx / n, tz / n], axis=1)      # unit direction of travel
        # what "normal" looks like at each point, for car- and track-independent features:
        #   curvature (1/m, smoothed over ~20 m) -> yaw rate the corner itself asks for
        #   typical acceleration (m/s^2) from the typical speed profile: a = v dv/ds
        ds = np.maximum(seg, 1e-3)
        head = np.arctan2(self.tan[:, 1], self.tan[:, 0])
        dh = (np.diff(np.r_[head, head[0]]) + np.pi) % (2 * np.pi) - np.pi
        k = dh / ds
        w = max(3, int(round(20.0 / max(float(np.median(ds)), 0.1))) | 1)
        self.curv = np.convolve(np.pad(k, w // 2, mode="wrap"), np.ones(w) / w, "valid")
        v = self.v / 3.6
        dv = np.diff(np.r_[v, v[0]])
        a = v * dv / ds
        self.acc_typ = np.convolve(np.pad(a, w // 2, mode="wrap"), np.ones(w) / w, "valid")
        normal = np.stack([-tz / n, tx / n], axis=1)
        # orient normals to point away from the track's centroid ("outside")
        cx, cz = self.x.mean(), self.z.mean()
        away = np.stack([self.x - cx, self.z - cz], axis=1)
        sign = np.sign((normal * away).sum(axis=1))
        self.outward = normal * np.where(sign == 0, 1, sign)[:, None]

    def idx(self, tp: float) -> int:
        return int(tp % 1.0 * self.bins) % self.bins

    def at(self, tp: float) -> np.ndarray:
        return self.xyz[self.idx(tp)].copy()

    def typical_speed(self, tp: float) -> float:
        return float(self.v[self.idx(tp)])

    def standoff(self, tp: float, metres: float, alt: float) -> np.ndarray:
        """Point beside the track (outside) at the given height above the surface."""
        i = self.idx(tp)
        p = self.xyz[i].copy()
        p[0] += self.outward[i, 0] * metres
        p[2] += self.outward[i, 1] * metres
        p[1] += alt
        return p

    def nearest(self, x: float, z: float) -> tuple[int, float]:
        d2 = (self.x - x) ** 2 + (self.z - z) ** 2
        i = int(np.argmin(d2))
        return i, float(np.sqrt(d2[i]))

    def advance(self, tp: float, metres: float) -> float:
        return (tp + metres / self.length) % 1.0
