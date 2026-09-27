"""Run bridge/replay, brain and localhost dashboard together. Ctrl+C stops all three."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from shared.config import BRAIN_WS_PORT, DASHBOARD_PORT, TELEMETRY_WS_PORT


def busy_ports():
    occupied = []
    for port in (TELEMETRY_WS_PORT, BRAIN_WS_PORT, DASHBOARD_PORT):
        for host in ("127.0.0.1", "::1"):
            try:
                with socket.create_connection((host, port), timeout=.2):
                    occupied.append(port)
                break
            except OSError:
                pass
    return occupied


def stop_child(child):
    if child.poll() is not None:
        return
    if os.name == "nt":
        # A Windows venv Python can launch a second interpreter process.
        # Terminating only the wrapper leaves the server and its port alive.
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if child.poll() is None:
        child.terminate()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--game-feeds", action="store_true", help="enable game feeds for this launch only")
    parser.add_argument("--session", default="data/sessions/synthetic_00")
    args = parser.parse_args()
    occupied = busy_ports()
    if occupied:
        print("Cannot start: demo ports already in use: " + ", ".join(map(str, occupied)), flush=True)
        print("Fly Marshal may already be running. Open http://localhost:8000, or press Ctrl+C "
              "in the previous launcher before starting again. Existing logs were preserved.", flush=True)
        return 1
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if args.game_feeds:
        env["MARSHAL_GAME_FEEDS"] = "1"
    from shared.settings import get
    if get('models.car.skin_manifest'):
        from tools.prepare_car_skins import prepare
        try:
            prepare()
        except (OSError, ValueError, ImportError) as exc:
            print(f'[skins] Could not update local skins: {exc}; using existing textures.', flush=True)
    stream = ["-m", "pipeline.live_bridge"] if args.live else [
        "-m", "pipeline.replay_stream", "--session", args.session, "--start", "40", "--loop"]
    commands = [stream, ["brain.py"], ["-m", "http.server", str(DASHBOARD_PORT), "--bind", "127.0.0.1", "--directory", "dashboard"]]
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
        for name, child in zip(("stream", "brain", "dashboard"), children):
            if child.poll() is not None:
                path = logdir / f"{name}.log"
                print(f"{name} stopped (exit {child.returncode}). Log: {path}", flush=True)
                print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]), flush=True)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            stop_child(child)
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        for handle in logs:
            handle.close()


if __name__ == "__main__":
    sys.exit(main())
