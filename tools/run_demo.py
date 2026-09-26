"""Run bridge/replay, brain and localhost dashboard together. Ctrl+C stops all three."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--game-feeds", action="store_true", help="enable game feeds for this launch only")
    parser.add_argument("--session", default="data/sessions/synthetic_00")
    args = parser.parse_args()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if args.game_feeds:
        env["MARSHAL_GAME_FEEDS"] = "1"
    stream = ["-m", "pipeline.live_bridge"] if args.live else [
        "-m", "pipeline.replay_stream", "--session", args.session, "--start", "40", "--loop"]
    commands = [stream, ["brain.py"], ["-m", "http.server", "8000", "--bind", "127.0.0.1", "--directory", "dashboard"]]
    logdir = ROOT / "data" / "runtime"
    logdir.mkdir(parents=True, exist_ok=True)
    children, logs = [], []
    try:
        for name, command in zip(("stream", "brain", "dashboard"), commands):
            handle = (logdir / f"{name}.log").open("w", encoding="utf-8")
            logs.append(handle)
            children.append(subprocess.Popen([sys.executable, *command], cwd=ROOT, env=env,
                stdout=handle, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        print("Dashboard: http://localhost:8000", flush=True)
        print(f"Process logs: {logdir}. Ctrl+C to stop.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        raise RuntimeError(f"A component stopped. Check {logdir}")
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        for handle in logs:
            handle.close()


if __name__ == "__main__":
    main()
