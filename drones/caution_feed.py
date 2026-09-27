"""Short-lived, display-only caution zones for the CSP HUD app."""
import math
import time
from pathlib import Path
from drones.game_feeds import atomic_json
from shared.settings import get


def caution_ranges(events, length):
    if not get('scene.yellow_zones_enabled', True) or length <= 0:
        return []
    before = max(0, float(get('scene.yellow_before_m', 200)))
    after = max(0, float(get('scene.yellow_after_m', 50)))
    ranges = []
    for event in events:
        p = event.get('track_pos')
        if event.get('type') != 'incident' or not isinstance(p, (int, float)) or not math.isfinite(p):
            continue
        if before + after >= length:
            return [[0, 1]]
        if before + after <= 0:
            continue
        start = (p - before / length) % 1
        end = start + (before + after) / length
        ranges.extend([[start, 1], [0, end - 1]] if end > 1 else [[start, end]])
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


class CautionFeed:
    def __init__(self):
        self.path = Path(get('live.ac_root', env='AC_ROOT')) / 'logs' / 'marshal_cautions.json'
        self.last_write = 0

    def publish(self, brain, session_id, track_id, live, age):
        # Never renew old warnings while telemetry is absent, or from a replay.
        if not live or age >= 2 or not get('scene.yellow_hud_enabled', True):
            return
        now = time.monotonic()
        if now - self.last_write < .1:
            return
        self.last_write = now
        atomic_json(self.path, dict(source='live', session_id=session_id, track_id=track_id,
                    sent_at=time.time(), ranges=caution_ranges(brain.active.values(), brain.track.length)))
