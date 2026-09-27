import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.live_bridge import LogTail, TrackBuilder


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pointer = self.root / '_active_recording.txt'
        self.tail = LogTail(self.root)

    def start(self, name):
        parts = self.root / (name + '.parts')
        parts.mkdir()
        self.pointer.write_text(str(parts), encoding='utf-8')
        self.assertTrue(self.tail.find_session())
        return parts

    def test_new_session_clears_every_old_car_and_pending_frame(self):
        self.start('spa')
        self.tail.frames.append((1, [{'car_id': 4}]))
        self.tail.pending[20] = [{}]
        self.tail.drivers[4] = 'Old driver'
        self.tail.car_models[4] = 'old_model'
        self.tail.slow[4] = (1, 5, 0)
        self.tail.meta = {'trackFull': 'spa'}
        self.tail.next_part = 99
        self.start('monza')
        self.assertEqual(self.tail.session, 2)
        self.assertEqual(self.tail.next_part, 1)
        for value in (self.tail.frames, self.tail.pending, self.tail.drivers, self.tail.car_models, self.tail.slow, self.tail.meta):
            self.assertFalse(value)

    def test_live_frames_preserve_model_for_speed_reference(self):
        self.tail.handle('CAR,1,{"driver":"Driver","car":"gt3"}')
        fields=['0']*25
        fields[0]='F'; fields[1]='1000'; fields[2]='1'; fields[7]='140'; fields[24]='0.25'
        self.tail.handle(','.join(fields))
        self.tail.flush_pending()
        self.assertEqual(self.tail.frames[0][1][0]['car_model'],'gt3')

    def test_part_removed_during_read_does_not_crash(self):
        parts = self.start('spa')
        part = parts / 'part_000001.txt'
        part.write_text('META,{"trackFull":"spa"}\n')
        with patch('pipeline.live_bridge.time.sleep', side_effect=lambda _: part.unlink()):
            self.assertEqual(self.tail.poll(), 0)
        self.assertEqual(self.tail.next_part, 1)

    def test_empty_pointer_is_transient_and_missing_directory_ends_session(self):
        parts = self.start('spa')
        self.pointer.write_text('')
        self.assertFalse(self.tail.find_session())
        self.assertEqual(self.tail.session, 1)
        self.pointer.write_text(str(parts))
        parts.rmdir()
        self.assertFalse(self.tail.find_session())
        self.assertIsNone(self.tail.parts_dir)
        self.assertEqual(self.tail.session, 2)

    def test_incomplete_chunk_is_retried(self):
        parts = self.start('spa')
        part = parts / 'part_000001.txt'
        part.write_text('META,{"trackFull":')
        self.tail.poll()
        self.assertEqual(self.tail.next_part, 1)
        part.write_text('META,{"trackFull":"spa"}\n')
        self.tail.poll()
        self.assertEqual(self.tail.meta['trackFull'], 'spa')
        self.assertEqual(self.tail.next_part, 2)

    def test_stationary_car_loads_new_ai_line_and_wrong_reference_is_ignored(self):
        builder = TrackBuilder(None, 'AC', 'auto')
        builder.reference_msg = json.dumps({'track_id': 'spa'})
        self.tail.meta = {'trackFull': 'monza'}
        car = dict(speed_kmh=0, track_pos=0, x=10, y=1, z=20)
        with patch('pipeline.live_bridge.track_source.ai_line_path', return_value='monza.ai'), \
                patch('pipeline.live_bridge.track_source.from_ai_line', return_value='NEW_TRACK') as build:
            self.assertEqual(builder.feed(self.tail, [car]), 'NEW_TRACK')
            build.assert_called_once_with('monza.ai', 'monza', [(0, 10, 20)])


if __name__ == '__main__':
    unittest.main()
