"""Stream a logged session over websocket as if it were live.

The demo uses this instead of a live AC race, so it is repeatable.
On connect each client gets a "track" message, then "frames" messages
(one per logged tick) in real time, scaled by --speed.

Usage:
  python -m pipeline.replay_stream                                   # synthetic session
  python -m pipeline.replay_stream --session data/sessions/spa_example --speed 2 --start 40 --loop
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import pandas as pd
from websockets.asyncio.server import broadcast, serve

from shared.config import DATA, TELEMETRY_WS_PORT
from shared.schemas import FRAME_FIELDS


def load(session: Path):
    df = pd.read_csv(session / "telemetry.csv")
    cl = pd.read_csv(session / "centerline.csv")
    if "y" not in cl.columns:
        cl["y"] = 0.0
    if "typical_speed_kmh" not in cl.columns:
        cl["typical_speed_kmh"] = 0.0
    seg = ((cl[["x", "z"]].diff().fillna(0) ** 2).sum(axis=1) ** 0.5).sum()
    track_msg = json.dumps({
        "type": "track",
        "length_m": float(seg),
        "centerline": cl[["track_pos", "x", "y", "z", "typical_speed_kmh"]].round(3).values.tolist(),
    })
    df["in_pit"] = df["in_pit"].astype(bool)
    ticks = []
    for t, g in df.groupby("t", sort=True):
        cars = g[FRAME_FIELDS].round(3).to_dict("records")
        ticks.append((float(t), json.dumps({"type": "frames", "t": float(t), "cars": cars})))
    return track_msg, ticks


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=Path, default=DATA / "sessions" / "synthetic_00", help="session folder")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    ap.add_argument("--start", type=float, default=0.0, help="start at this session time (s)")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--port", type=int, default=TELEMETRY_WS_PORT)
    args = ap.parse_args()

    track_msg, ticks = load(args.session)
    ticks = [tk for tk in ticks if tk[0] >= args.start]
    print(f"loaded {len(ticks)} ticks from {args.session}")

    async def handler(ws):
        await ws.send(track_msg)
        await ws.wait_closed()

    async with serve(handler, "localhost", args.port) as server:
        print(f"streaming on ws://localhost:{args.port}  (speed x{args.speed})")
        while True:
            t0_session, t0_wall = ticks[0][0], time.perf_counter()
            for t, msg in ticks:
                delay = (t - t0_session) / args.speed - (time.perf_counter() - t0_wall)
                if delay > 0:
                    await asyncio.sleep(delay)
                broadcast(server.connections, msg)
            print("session finished")
            if not args.loop:
                break
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
