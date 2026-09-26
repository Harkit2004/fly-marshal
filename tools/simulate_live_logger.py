"""Pretend to be the VRC Race Logger mid-race, for testing live mode without AC.

Replays an existing vrclog .txt in real time as chunk files, exactly like the
logger does during a session:
  <logs>/vrclog_sim.parts/part_000001.txt, part_000002.txt, ...   (one per --flush seconds)
  <logs>/_active_recording.txt                                     (pointer to the parts dir)

Usage:
  python tools/simulate_live_logger.py data/raw/vrclog_...spa...txt --logs data/live_logs --flush 2
  python -m pipeline.live_bridge --logs-dir data/live_logs --reference data/sessions/spa_example
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--logs", type=Path, default=Path("data/live_logs"))
    ap.add_argument("--flush", type=float, default=2.0, help="seconds per chunk (logger default is 10)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--start", type=float, default=0.0, help="skip to this session time (s)")
    args = ap.parse_args()

    parts = args.logs / "vrclog_sim.parts"
    shutil.rmtree(parts, ignore_errors=True)
    parts.mkdir(parents=True)
    (args.logs / "_active_recording.txt").write_text(f"{parts.resolve()}\n{(args.logs / 'vrclog_sim.txt').resolve()}")

    header, body = [], []
    for line in args.log.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
        kind = line.split(",", 1)[0]
        if kind in ("VRCLOG", "META", "CAR", "ZONES", "ENERGY"):
            header.append(line)
        elif kind != "END":
            try:
                t = int(line.split(",", 2)[1]) / 1000.0
            except (IndexError, ValueError):
                continue
            if t >= args.start:
                body.append((t, line))

    idx, buf = 1, list(header)
    t0_log, t0_wall = (body[0][0] if body else 0.0), time.perf_counter()
    next_flush = t0_log + args.flush
    print(f"writing chunks to {parts} every {args.flush}s (Ctrl+C to stop)")
    for t, line in body:
        if t >= next_flush:
            wait = (next_flush - t0_log) / args.speed - (time.perf_counter() - t0_wall)
            if wait > 0:
                time.sleep(wait)
            (parts / f"part_{idx:06d}.txt").write_text("".join(buf), encoding="utf-8")
            print(f"  part {idx:4d}  session t={next_flush:7.1f}s  {len(buf):5d} lines")
            idx, buf, next_flush = idx + 1, [], next_flush + args.flush
        buf.append(line)
    if buf:
        (parts / f"part_{idx:06d}.txt").write_text("".join(buf), encoding="utf-8")
    (args.logs / "_active_recording.txt").unlink(missing_ok=True)
    print("session finished")


if __name__ == "__main__":
    main()
