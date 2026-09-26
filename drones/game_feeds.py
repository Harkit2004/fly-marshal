"""File-based CSP camera bridge. Only live telemetry may request game frames."""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from shared.settings import ROOT, get


def frames_directory() -> Path:
    configured = get("game_feeds.frames_dir", "")
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else ROOT / path
    return Path(get("live.ac_root", env="AC_ROOT")) / "logs" / "marshal_cams"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class GameFrame:
    drone_id: int
    event_id: str | None
    run_id: str
    filename: str
    jpeg: bytes
    captured_at: float

    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode("ascii")


class GameFeeds:
    def __init__(self):
        self.enabled = bool(get("game_feeds.enabled", False)) or os.environ.get("MARSHAL_GAME_FEEDS") == "1"
        self.directory = frames_directory()
        self.inbox = Path(get("live.ac_root", env="AC_ROOT")) / "logs" / "marshal_poses.json"
        self.run_id = uuid.uuid4().hex
        self.seq = 0
        self.last_write = 0.0
        self.last_frame_poll = 0.0
        self.live = False
        self.frames: dict[int, GameFrame] = {}
        self.sent: dict[int, str] = {}
        self.error: str | None = None
        self._jpeg_cache = {}

    def reset(self, live: bool) -> None:
        self.run_id = uuid.uuid4().hex
        self.live = live
        self.frames.clear()
        self.sent.clear()
        self.last_write = 0
        self._jpeg_cache.clear()

    def publish_poses(self, brain, cars: list[dict]) -> None:
        if not self.enabled or not self.live:
            return
        now = time.monotonic()
        if now - self.last_write < 0.1:
            return
        self.last_write = now
        by_id = {c["car_id"]: c for c in cars}
        events = {ev["id"]: ev for ev in brain.active.values()}
        poses = []
        for drone in brain.drones:
            pos = drone.pos.copy()
            pos[1] -= 0.6 * float(get("scene.drone_scale", 3.0))
            event = events.get(drone.event_id)
            car = None
            if event:
                look = np.array([event[k] for k in ("x", "y", "z")], dtype=float)
                car = by_id.get(event["car_ids"][0])
                if car:
                    cp = np.array([car[k] for k in ("x", "y", "z")], dtype=float)
                    if np.linalg.norm(cp - look) < 400:
                        look = cp
                    else:
                        car = None
            else:
                idx, _ = brain.track.nearest(float(pos[0]), float(pos[2]))
                tp = brain.track.advance(float(brain.track.tp[idx]), -60)
                look = brain.track.at(tp)
            poses.append({"drone_id": drone.id, "pos": pos.tolist(), "look_at": look.tolist(),
                          "fov": 55, "event_id": drone.event_id,
                          "car_id": car["car_id"] if car else None})
        self.seq += 1
        packet = {"enabled": True, "source": "live", "run_id": self.run_id, "seq": self.seq,
                  "sent_at": time.time(), "frames_dir": self.directory.resolve().as_posix(),
                  "method": get("game_feeds.method", "geometry_shot"),
                  "width": int(get("game_feeds.width", 640)), "height": int(get("game_feeds.height", 360)),
                  "fps": float(get("game_feeds.fps", 4)),
                  "patrol_fps": float(get("game_feeds.patrol_fps", 2)),
                  "selected_drone": int(get("game_feeds.selected_drone", 0)), "drones": poses}
        try:
            atomic_json(self.inbox, packet)
            self.error = None
        except (OSError, ValueError) as exc:
            self.error = f"Pose write failed: {type(exc).__name__}"

    def read_frame(self, drone_id: int) -> GameFrame | None:
        if not self.enabled or not self.live:
            return None
        try:
            meta_path = self.directory / f"drone_{drone_id}.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            name = meta["filename"]
            if (meta.get("source") != "game" or meta.get("run_id") != self.run_id
                    or meta.get("drone_id") != drone_id
                    or not re.fullmatch(rf"drone_{drone_id}_{self.run_id}_\d+\.jpg", name)):
                return None
            path = self.directory / name
            stat = path.stat()
            stamp = stat.st_mtime
            if not 0 <= time.time() - stamp < 2:
                return None
            if stat.st_size > 8_000_000:
                return None
            key = (name, stat.st_mtime_ns, stat.st_size, meta.get("event_id"))
            cached = self._jpeg_cache.get(drone_id)
            if cached and cached[0] == key:
                return cached[1]
            # Metadata from a different process or session cannot authorize a frame.
            data = path.read_bytes()
            if len(data) > 8_000_000 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                return None
            frame = GameFrame(drone_id, meta.get("event_id"), self.run_id, name, data, stamp)
            self._jpeg_cache[drone_id] = (key, frame)
            return frame
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def poll_message(self, drone_ids: list[int]) -> str | None:
        if not self.enabled or not self.live:
            return None
        if time.monotonic() - self.last_frame_poll < 0.1:
            return None
        self.last_frame_poll = time.monotonic()
        updates = []
        for drone_id in drone_ids:
            frame = self.read_frame(drone_id)
            if frame is None:
                self.frames.pop(drone_id, None)
                continue
            self.frames[drone_id] = frame
            if self.sent.get(drone_id) != frame.filename:
                updates.append({"drone_id": drone_id, "source": "game", "event_id": frame.event_id,
                                "age_s": max(0, time.time() - frame.captured_at), "image": frame.data_url()})
                self.sent[drone_id] = frame.filename
        return json.dumps({"type": "game_frames", "frames": updates}) if updates else None
