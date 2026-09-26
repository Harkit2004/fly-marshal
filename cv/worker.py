"""Bounded background vision work; no requests or waits in the drone control tick."""
import asyncio
import logging
import time
import numpy as np

from cv.report import vision_report
from shared.schemas import message
from shared.settings import get

log = logging.getLogger(__name__)


class VisionWorker:
    def __init__(self):
        self.task = None
        self.event_id = None
        self.run_id = None
        self.started = 0.0
        self.expired = False
        self.done = set()

    def reset(self):
        # Do not create another HTTP thread while an old one is still running.
        self.done.clear()
        self.expired = True

    def poll(self, brain, feeds, broadcast):
        active = {e["id"]: e for e in brain.active.values()}
        if self.task is not None:
            if time.monotonic() - self.started > float(get("vision.timeout_s", 8)):
                if not self.expired:
                    log.warning("Vision timed out for %s; keeping telemetry report", self.event_id)
                self.expired = True
            if self.task.done():
                try:
                    report = self.task.result()
                    if report and not self.expired and self.run_id == feeds.run_id and self.event_id in active:
                        broadcast(message("report", report=report))
                except Exception as exc:
                    # Do not print provider responses, headers or secrets.
                    log.warning("Vision unavailable for %s (%s)", self.event_id, type(exc).__name__)
                self.task = None
            else:
                return
        if not get("vision.enabled") or not feeds.enabled or not feeds.live:
            return
        for drone in brain.drones:
            event = active.get(drone.event_id)
            token = (feeds.run_id, drone.event_id)
            if not event or event["type"] != "incident" or drone.mode not in ("hold", "escort") or token in self.done:
                continue
            if drone.target is None or np.linalg.norm(drone.pos - drone.target) > 8:
                continue  # 'hold' is assigned before arrival, so check actual distance.
            frame = feeds.read_frame(drone.id)
            if not frame or frame.event_id != event["id"]:
                continue
            self.event_id, self.run_id = event["id"], feeds.run_id
            self.done.add(token)
            self.started, self.expired = time.monotonic(), False
            self.task = asyncio.create_task(asyncio.to_thread(vision_report, event["id"], frame))
            break
