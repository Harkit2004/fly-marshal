"""Message formats shared by every component.

Coordinates are Assetto Corsa world coordinates in metres, y is up, the
ground plane is x/z. track_pos is AC's NormalizedSplinePosition (0-1).

Every websocket message is a JSON object with a "type" field:
  "track"   {centerline: [[track_pos, x, y, z, typical_speed_kmh], ...], length_m}
  "frames"  {t, cars: [TelemetryFrame, ...]}        on TELEMETRY_WS_PORT
  "risk"    {event: RiskEvent}                       on BRAIN_WS_PORT
  "drones"  {t, drones: [DroneState, ...]}           on BRAIN_WS_PORT
  "report"  {report: IncidentReport}                 on BRAIN_WS_PORT
  "risk_end" {id}                                     on BRAIN_WS_PORT

Change this file only when both of you agree.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

# Session folder: data/sessions/<name>/{telemetry.csv, events.csv, centerline.csv, meta.json}
# Produced by pipeline/vrclog_adapter.py (real AC logs) or tools/make_synthetic.py.
#
# telemetry.csv columns. Most come straight from the VRC Race Logger F stream
# (15 Hz, every car); lap / position / in_pit are held from its 1 Hz S stream.
TELEMETRY_COLUMNS = [
    "t", "car_id", "driver", "car_model",
    "x", "y", "z", "speed_kmh", "heading_deg", "yaw_rate",   # yaw_rate rad/s, heading -180..180
    "acc_x", "acc_y", "acc_z",                              # G: lateral, vertical, longitudinal
    "gas", "brake", "steer", "gear",
    "wheels_out", "surf",                                   # wheels off track 0-4, surface nibbles
    "track_pos", "lap", "position", "in_pit",
]

# events.csv: ground-truth incidents for labels and evaluation.
#   source = detector (logger analyzer episodes) | coll (car-to-car contact) | logger | synthetic
#   kind   = spin / slide / stuck / off_* / dnf / contact / retire / stopped / limp / rejoin
EVENT_COLUMNS = ["t", "car_id", "kind", "other_car", "severity", "source", "episode"]

DroneMode = Literal["patrol", "intercept", "escort", "hold"]
PilotKind = Literal["fly", "pid"]


@dataclass
class TelemetryFrame:
    t: float
    car_id: int
    driver: str
    x: float
    y: float
    z: float
    speed_kmh: float
    yaw_rate: float
    wheels_out: int
    track_pos: float
    lap: int
    in_pit: bool


FRAME_FIELDS = list(TelemetryFrame.__dataclass_fields__)[1:]   # per-car fields in a "frames" message


@dataclass
class RiskEvent:
    id: str
    t: float
    type: Literal["predicted", "incident"]
    kind: str                      # spin / stopped / slow / closing / ...
    car_ids: list[int]
    x: float
    y: float
    z: float
    track_pos: float
    severity: float                # 0-1
    eta_s: float = 0.0             # seconds until predicted event, 0 for incidents


@dataclass
class DroneCommand:
    drone_id: int
    mode: DroneMode
    target: Optional[tuple[float, float, float]] = None
    follow_car_id: Optional[int] = None
    event_id: Optional[str] = None


@dataclass
class DroneState:
    t: float
    drone_id: int
    pilot: PilotKind
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    mode: DroneMode
    target: Optional[tuple[float, float, float]] = None
    event_id: Optional[str] = None
    fly_activity: dict = field(default_factory=dict)   # {forward, turn, climb, spikes}


@dataclass
class IncidentReport:
    event_id: str
    stopped: bool
    on_racing_line: bool
    debris: Optional[bool]          # None = unknown (only a camera / vision model can tell)
    smoke: Optional[bool]
    cars_approaching_s: Optional[float]
    summary: str
    frame_path: Optional[str] = None


def message(kind: str, **payload) -> str:
    """Serialise a websocket message. Dataclasses inside payload are converted."""
    def conv(v):
        if hasattr(v, "__dataclass_fields__"):
            return asdict(v)
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v
    return json.dumps({"type": kind, **{k: conv(v) for k, v in payload.items()}})
