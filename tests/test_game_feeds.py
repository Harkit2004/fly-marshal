import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from drones.game_feeds import GameFeeds, GameFrame, atomic_json
from cv.report import vision_report, parse_visual, VISUAL_FIELDS
from cv.worker import VisionWorker
from shared.settings import SETTINGS
from shared.track import Track


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.dict(SETTINGS, {"game_feeds": {"enabled": True, "frames_dir": str(self.root)}})
        self.patch.start()
        self.feeds = GameFeeds()
        self.feeds.inbox = self.root / "poses.json"
        self.feeds.reset(True)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def capture(self, **overrides):
        name = f"drone_0_{self.feeds.run_id}_1.jpg"
        (self.root / name).write_bytes(b"\xff\xd8test\xff\xd9")
        meta = dict(source="game", run_id=self.feeds.run_id, drone_id=0,
                    event_id="inc-0-20", filename=name)
        meta.update(overrides)
        atomic_json(self.root / "drone_0.json", meta)
        return self.root / name

    def test_live_frame_and_websocket_payload(self):
        self.capture()
        frame = self.feeds.read_frame(0)
        self.assertEqual(frame.event_id, "inc-0-20")
        m = json.loads(self.feeds.poll_message([0]))
        self.assertEqual(m["frames"][0]["source"], "game")
        self.assertTrue(m["frames"][0]["image"].startswith("data:image/jpeg;base64,"))

    def test_replay_and_disabled_never_read_game_frames(self):
        self.capture()
        self.feeds.live = False
        self.assertIsNone(self.feeds.read_frame(0))
        self.feeds.live = True
        self.feeds.enabled = False
        self.assertIsNone(self.feeds.read_frame(0))

    def test_stale_wrong_run_traversal_and_corrupt_jpeg_rejected(self):
        p = self.capture()
        os.utime(p, (time.time() - 3, time.time() - 3))
        self.assertIsNone(self.feeds.read_frame(0))
        self.capture(run_id="other")
        self.assertIsNone(self.feeds.read_frame(0))
        self.capture(filename="../secret.jpg")
        self.assertIsNone(self.feeds.read_frame(0))
        p = self.capture()
        p.write_bytes(b"\xff\xd8partial")
        self.assertIsNone(self.feeds.read_frame(0))

    def test_session_reset_rejects_old_frames(self):
        self.capture()
        self.feeds.reset(True)
        self.assertIsNone(self.feeds.read_frame(0))

    def test_unchanged_jpeg_read_once_but_stale_cache_rejected(self):
        path = self.capture()
        original = Path.read_bytes
        with patch.object(Path, 'read_bytes', autospec=True, side_effect=original) as read:
            first = self.feeds.read_frame(0)
            self.assertIs(self.feeds.read_frame(0), first)
            self.assertEqual(read.call_count, 1)
            os.utime(path, (time.time() - 3, time.time() - 3))
            self.assertIsNone(self.feeds.read_frame(0))
        self.feeds.reset(True)
        self.assertFalse(self.feeds._jpeg_cache)

    @patch('drones.game_feeds.time.monotonic', return_value=100.0)
    def test_pose_coordinates_aim_and_throttle(self, _clock):
        track = Track([[0, 0, 0, 0, 100], [.25, 100, 0, 0, 100], [.5, 100, 0, 100, 100], [.75, 0, 0, 100, 100]])
        event = dict(id="inc-0-20", car_ids=[0], x=0, y=0, z=0)
        drone = SimpleNamespace(id=0, event_id=event["id"], pos=np.array([1., 25., 2.]))
        brain = SimpleNamespace(active={0: event}, drones=[drone], track=track)
        self.feeds.publish_poses(brain, [dict(car_id=0, x=3, y=0, z=5)])
        p = json.loads(self.feeds.inbox.read_text())
        self.assertEqual(p["drones"][0]["look_at"], [3, 0, 5])
        self.assertEqual(p["drones"][0]["car_id"], 0)
        self.feeds.publish_poses(brain, [])
        self.assertEqual(self.feeds.seq, 1)
        self.feeds.reset(False)
        self.feeds.publish_poses(brain, [])
        self.assertEqual(self.feeds.seq, 1)


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = patch.dict(SETTINGS, {"vision": {"enabled": True, "provider": "openai", "model": "gpt-6-luna",
                           "cache_dir": self.tmp.name, "api_key_env": "TEST_VISION_KEY", "timeout_s": .02}})
        self.config.start()
        self.key = patch.dict(os.environ, {"TEST_VISION_KEY": "unit-test-only"})
        self.key.start()
        self.frame = GameFrame(0, "inc-0-20", "abc", "frame.jpg", b"\xff\xd8test\xff\xd9", time.time())
        self.visual = {**dict.fromkeys(VISUAL_FIELDS), "summary": "<script>untrusted text</script>"}

    def tearDown(self):
        self.config.stop(); self.key.stop(); self.tmp.cleanup()

    def test_reject_non_game_stale_and_wrong_event_without_request(self):
        with patch("urllib.request.urlopen") as request:
            self.assertIsNone(vision_report("inc-0-20", Path("threejs.jpg")))
            self.assertIsNone(vision_report("other-event", self.frame))
            stale = GameFrame(0, "inc-0-20", "abc", "frame.jpg", self.frame.jpeg, time.time() - 3)
            self.assertIsNone(vision_report("inc-0-20", stale))
            request.assert_not_called()

    def test_strict_schema_request_and_provenance_cache(self):
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(self.visual)}]}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as request:
            report = vision_report("inc-0-20", self.frame)
            self.assertEqual(report.source, "game_vision")
            self.assertIsNone(report.blocking)
            payload = json.loads(request.call_args.args[0].data)
            self.assertTrue(payload["text"]["format"]["strict"])
            self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        with patch("urllib.request.urlopen") as request:
            self.assertEqual(vision_report("inc-0-20", self.frame).summary, self.visual["summary"])
            request.assert_not_called()

    def test_invalid_types_rejected(self):
        with self.assertRaises(ValueError):
            parse_visual({**self.visual, "blocking": "false"})
        with self.assertRaises(ValueError):
            parse_visual({**self.visual, "extra": True})

    def test_worker_waits_for_arrival_and_ignores_timeout(self):
        async def run():
            worker = VisionWorker()
            drone = SimpleNamespace(id=0, event_id="inc-0-20", mode="hold",
                                    pos=np.array([100., 0, 0]), target=np.zeros(3))
            brain = SimpleNamespace(active={0: {"id": drone.event_id, "type": "incident"}}, drones=[drone])
            feeds = SimpleNamespace(run_id="abc", enabled=True, live=True, read_frame=lambda _: self.frame)
            messages = []
            def slow(*args):
                time.sleep(.08)
                return SimpleNamespace()
            with patch("cv.worker.vision_report", side_effect=slow) as request:
                worker.poll(brain, feeds, messages.append)
                self.assertIsNone(worker.task)
                drone.pos = np.zeros(3)
                worker.poll(brain, feeds, messages.append)
                await asyncio.sleep(.04)
                worker.poll(brain, feeds, messages.append)
                self.assertTrue(worker.expired)
                await worker.task
                worker.poll(brain, feeds, messages.append)
                self.assertEqual(messages, [])
                self.assertEqual(request.call_count, 1)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
