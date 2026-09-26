"""Shared constants. Change these together, not in one module."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

TELEMETRY_WS_PORT = 8765   # pipeline/replay_stream.py -> brain.py, dashboard
BRAIN_WS_PORT = 8766       # brain.py -> dashboard
DASHBOARD_PORT = 8000      # python -m http.server

SAMPLE_HZ = 15             # VRC Race Logger default fast-stream rate
CENTERLINE_BINS = 2000     # bins of track_pos in centerline.csv

# Drones: tune these in settings.toml [drones]
from shared.settings import get as _get

DRONE_COUNT = int(_get("drones.count"))
DRONE_MAX_SPEED = float(_get("drones.max_speed_mps"))
DRONE_MAX_ACCEL = float(_get("drones.max_accel_mps2"))
DRONE_MIN_ALT = float(_get("drones.min_alt_m"))          # m above the track surface (AC y is up)
DRONE_MAX_ALT = float(_get("drones.max_alt_m"))
DRONE_PATROL_ALT = float(_get("drones.patrol_alt_m"))
DRONE_MIN_SEPARATION = float(_get("drones.min_separation_m"))
DRONE_TRACK_CLEARANCE = float(_get("drones.track_clearance_m"))   # min horizontal distance from centreline
