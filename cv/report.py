"""Incident reports.

telemetry_report()  instant preliminary report from telemetry alone (works now).
vision_report()     TODO(teammate): send the drone-view frame to a vision model and
                    ask for strict JSON matching IncidentReport. Cache results in
                    cv/cache/<event_id>.json so the demo never depends on the network.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from shared.schemas import IncidentReport

CACHE = Path(__file__).resolve().parent / "cache"


def telemetry_report(event: dict, f: dict) -> IncidentReport:
    stopped = f["speed_kmh"] < 5
    on_line = f["off_line_m"] < 6
    closing = f.get("closing_behind_mps", 0.0)
    approach = round(f["gap_behind_m"] / closing, 1) if closing > 1 else None
    parts = [f"Car #{event['car_ids'][0]} {event['kind']}"]
    parts.append("stationary" if stopped else f"moving at {f['speed_kmh']:.0f} km/h")
    parts.append("ON racing line" if on_line else f"{f['off_line_m']:.0f} m off line")
    if approach is not None:
        parts.append(f"next car arrives in {approach} s")
    return IncidentReport(
        event_id=event["id"], stopped=stopped, on_racing_line=on_line, debris=None, smoke=None,   # telemetry can't see these
        cars_approaching_s=approach, summary=", ".join(parts), frame_path=None,
    )


def vision_report(event_id: str, frame_path: Path) -> IncidentReport | None:
    cached = CACHE / f"{event_id}.json"
    if cached.exists():
        return IncidentReport(**json.loads(cached.read_text()))
    # TODO(teammate): call the vision model here, parse JSON into IncidentReport,
    # then save: cached.write_text(json.dumps(asdict(report)))
    return None


__all__ = ["telemetry_report", "vision_report", "asdict"]
