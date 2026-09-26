"""Shared constants. Change these together, not in one module."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

TELEMETRY_WS_PORT = 8765   # pipeline/replay_stream.py -> brain.py, dashboard
BRAIN_WS_PORT = 8766       # brain.py -> dashboard
DASHBOARD_PORT = 8000      # python -m http.server

SAMPLE_HZ = 15             # VRC Race Logger default fast-stream rate
CENTERLINE_BINS = 2000     # bins of track_pos in centerline.csv

# Drones
DRONE_COUNT = 3
DRONE_MAX_SPEED = 25.0     # m/s (~90 km/h)
DRONE_MAX_ACCEL = 8.0      # m/s^2
DRONE_MIN_ALT = 15.0       # m above the track surface (AC y is up)
DRONE_MAX_ALT = 60.0
DRONE_PATROL_ALT = 25.0
DRONE_MIN_SEPARATION = 10.0
DRONE_TRACK_CLEARANCE = 12.0   # min horizontal distance from centreline

# Fly brain visualisation: brain.py samples this many neurons per region every tick and
# sends which of them fired. The dashboard lays out the same regions in the same order.
FLY_REGIONS = ["photo_l", "photo_r", "motion_l", "motion_r", "central", "descending"]
FLY_SAMPLE_PER_REGION = 250
