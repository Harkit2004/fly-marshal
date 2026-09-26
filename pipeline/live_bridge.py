"""Live mode: stream a race that is running in AC right now.

The VRC Race Logger writes the running session as chunk files
(<AC>/logs/vrclog_*.parts/part_NNNNNN.txt) and points at them from
<AC>/logs/_active_recording.txt. This bridge tails those chunks and publishes
the same websocket messages as pipeline/replay_stream.py, so brain.py and the
dashboard don't know or care whether the race is live or replayed.

Latency = the logger's chunk interval (FLUSH_SECONDS = 10 in vrc_race_logger.lua)
plus --delay. For a snappier live demo, set FLUSH_SECONDS = 1 in your *installed*
copy (<AC>/apps/lua/vrc_race_logger/vrc_race_logger.lua), not in third_party/.
Playback runs --delay seconds behind the newest data so it stays smooth between chunks.

The track (centreline, widths, typical speed per track_pos) comes from, in order:
  --reference <session>   a session already converted from the same track (best typical speeds)
  the track's AI line     <AC>/content/tracks/<track>/<layout>/ai/fast_lane.ai
  the first lap           learnt from the cars' positions (custom tracks without an AI line)
(settings.toml live.track_source picks between the last two.) AC root, delay and track source
all live in settings.toml [live]; the flags below override them.

  python -m pipeline.live_bridge
  python -m pipeline.live_bridge --reference data/sessions/our_track_clean_01
  python -m pipeline.live_bridge --logs-dir <folder with _active_recording.txt>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path

from websockets.asyncio.server import broadcast, serve

from pipeline import track_source
from pipeline.replay_stream import track_message
from shared.config import TELEMETRY_WS_PORT
from shared.settings import get

# F line: F,t,car,posX,posY,posZ,compass,speedKmh,gas,brake,steer,gear,vLocX,vLocZ,yawRate,
#         accX,accY,accZ,nd0,nd1,nd2,nd3,wheelsOut,surfHex,spline
# S line: S,t,car,fuel,tc0-3,pr0-3,wear0-3,dmg0-4,engineLife,gearboxDmg,racePos,lap,flags,...
S_RACEPOS, S_LAP, S_FLAGS = 23, 24, 25
PIT_FLAGS = 1 | 2


class LogTail:
    """Follows the active recording's part files and turns F/S lines into frames."""

    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.parts_dir: Path | None = None
        self.next_part = 1
        self.drivers: dict[int, str] = {}
        self.slow: dict[int, tuple[int, int, int]] = {}      # car -> (race_pos, lap, flags)
        self.pending: dict[int, list] = {}                     # t_ms -> cars (current, incomplete tick)
        self.frames: deque = deque()                           # (t_s, cars)
        self.meta: dict = {}

    def find_session(self) -> bool:
        ptr = self.logs_dir / "_active_recording.txt"
        if not ptr.exists():
            return False
        parts = Path(ptr.read_text(encoding="utf-8").splitlines()[0].strip())
        if parts != self.parts_dir:
            print(f"[live] following {parts.name}")
            self.parts_dir, self.next_part = parts, 1
            self.drivers, self.slow, self.pending = {}, {}, {}
            self.frames.clear()
        return parts.exists()

    def poll(self) -> int:
        """Read any new complete part files. Returns number of new frames."""
        if not self.parts_dir:
            return 0
        before = len(self.frames)
        while True:
            path = self.parts_dir / f"part_{self.next_part:06d}.txt"
            nxt = self.parts_dir / f"part_{self.next_part + 1:06d}.txt"
            if not path.exists():
                break
            # a part is written in one go (io.saveAsync); wait until its size is stable
            size = path.stat().st_size
            time.sleep(0.05)
            if path.stat().st_size != size and not nxt.exists():
                break
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                self.handle(line)
            self.next_part += 1
        return len(self.frames) - before

    def handle(self, line: str) -> None:
        if line.startswith("F,"):
            f = line.split(",")
            t, car = int(f[1]), int(f[2])
            if self.pending and t not in self.pending:
                self.flush_pending()
            pos, lap, flags = self.slow.get(car, (0, 0, 0))
            self.pending.setdefault(t, []).append({
                "car_id": car, "driver": self.drivers.get(car, f"car{car}"),
                "x": float(f[3]), "y": float(f[4]), "z": float(f[5]), "speed_kmh": float(f[7]),
                "yaw_rate": float(f[14]), "wheels_out": int(float(f[22])), "track_pos": float(f[24]),
                "lap": lap, "in_pit": bool(flags & PIT_FLAGS),
            })
        elif line.startswith("S,"):
            s = line.split(",")
            try:
                self.slow[int(s[2])] = (int(float(s[S_RACEPOS])), int(float(s[S_LAP])), int(float(s[S_FLAGS])))
            except (IndexError, ValueError):
                pass
        elif line.startswith("CAR,"):
            _, idx, js = line.split(",", 2)
            self.drivers[int(idx)] = json.loads(js).get("driver", f"car{idx}")
        elif line.startswith("META,"):
            self.meta = json.loads(line.split(",", 1)[1])
            print(f"[live] {self.meta.get('trackFull')} · {self.meta.get('sessionName')} · {self.meta.get('cars')} cars")

    def flush_pending(self) -> None:
        for t, cars in sorted(self.pending.items()):
            self.frames.append((t / 1000.0, cars))
        self.pending = {}


class TrackBuilder:
    """Produces the track message once enough is known about the live session."""

    def __init__(self, reference: Path | None, ac_root: str | None, source: str):
        self.ac_root, self.source = ac_root, source
        self.msg = track_message(reference) if reference else None
        self.first_lap = track_source.FirstLap()
        self.samples: list = []
        self.said = None

    def say(self, text):
        if text != self.said:
            print(f"[live] {text}")
            self.said = text

    def feed(self, tail: LogTail, cars: list) -> str | None:
        """Call with every frame; returns the message the first time the track is ready."""
        if self.msg:
            return None
        track_full = tail.meta.get("trackFull", "")
        if not track_full:
            return None
        for c in cars:
            self.first_lap.add(c)
            if len(self.samples) < 200 and c["speed_kmh"] > 30:
                self.samples.append((c["track_pos"], c["x"], c["z"]))
        ai = track_source.ai_line_path(track_full, self.ac_root) if self.source != "first_lap" else None
        if ai and len(self.samples) >= 30:
            self.msg = track_source.from_ai_line(ai, track_full, self.samples)
            self.say(f"track from AI line: {ai}")
            return self.msg
        if not ai and self.source == "ai_line":
            self.say(f"no fast_lane.ai for {track_full} under {self.ac_root}; set live.track_source = \"auto\"")
            return None
        if not ai:
            cov = self.first_lap.coverage
            self.say(f"learning {track_full} from the first lap: {int(cov * 10) * 10}% of the lap seen")
            if cov >= track_source.FIRST_LAP_COVERAGE:
                self.msg = self.first_lap.message(track_full)
                self.say(f"track learnt from the first lap ({track_full})")
                return self.msg
        return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ac-root", default=os.environ.get("AC_ROOT") or get("live.ac_root"))
    ap.add_argument("--logs-dir", type=Path, help="defaults to <ac-root>/logs")
    ap.add_argument("--reference", type=Path, help="converted session folder of the same track (optional)")
    ap.add_argument("--track-source", default=get("live.track_source"), choices=["auto", "ai_line", "first_lap"])
    ap.add_argument("--delay", type=float, default=float(get("live.delay_s")), help="seconds to stay behind the newest data")
    ap.add_argument("--port", type=int, default=TELEMETRY_WS_PORT)
    args = ap.parse_args()
    logs = args.logs_dir or (Path(args.ac_root) / "logs" if args.ac_root else None)
    if not logs:
        ap.error("pass --ac-root (or set live.ac_root in settings.toml) or --logs-dir")

    tail = LogTail(logs)
    track = TrackBuilder(args.reference, args.ac_root, args.track_source)

    async def handler(ws):
        if track.msg:
            await ws.send(track.msg)
        await ws.wait_closed()

    async with serve(handler, "localhost", args.port) as server:
        print(f"[live] watching {logs} · streaming on ws://localhost:{args.port}")
        if not (logs / "_active_recording.txt").exists():
            print("[live] no active recording yet: start a session in AC with the VRC Race Logger app enabled")
        clock0 = None                      # (session_t, wall) anchor for smooth playout
        while True:
            if not tail.find_session():
                await asyncio.sleep(1.0)
                continue
            if tail.poll() and clock0 is None and tail.frames:
                newest = tail.frames[-1][0]
                clock0 = (newest - args.delay, time.perf_counter())
            if clock0:
                now_t = clock0[0] + (time.perf_counter() - clock0[1])
                newest = tail.frames[-1][0] if tail.frames else now_t
                if now_t > newest:        # ran dry (chunk late): hold, then resync
                    clock0 = (newest, time.perf_counter())
                while tail.frames and tail.frames[0][0] <= now_t:
                    t, cars = tail.frames.popleft()
                    new_track = track.feed(tail, cars)
                    if new_track:
                        broadcast(server.connections, new_track)
                    if track.msg:          # brain + dashboard need the track before frames mean anything
                        broadcast(server.connections, json.dumps({"type": "frames", "t": t, "cars": cars}))
            await asyncio.sleep(1 / 60)


if __name__ == "__main__":
    asyncio.run(main())
