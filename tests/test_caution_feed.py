import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from drones.caution_feed import CautionFeed, caution_ranges


class CautionTests(unittest.TestCase):
    def test_wrap_overlap_and_predictions(self):
        events = [dict(type='incident', track_pos=.1),dict(type='incident', track_pos=.2)]
        ranges = [[round(x,6) for x in r] for r in caution_ranges(events,1000)]
        self.assertEqual(ranges,[[0,.25],[.9,1]])
        self.assertEqual(caution_ranges([dict(type='predicted',track_pos=.5)],1000),[])

    def test_live_only_fresh_and_clear(self):
        with TemporaryDirectory() as temp:
            feed=CautionFeed(); feed.path=Path(temp)/'flags.json'
            brain=SimpleNamespace(active={0:dict(type='incident',track_pos=.5)},track=SimpleNamespace(length=1000))
            feed.publish(brain,'run','track',False,0)
            feed.publish(brain,'run','track',True,3)
            self.assertFalse(feed.path.exists())
            feed.publish(brain,'run','track',True,0)
            self.assertEqual(json.loads(feed.path.read_text())['source'],'live')
            brain.active.clear(); feed.last_write=0
            feed.publish(brain,'run','track',True,0)
            self.assertEqual(json.loads(feed.path.read_text())['ranges'],[])

    def test_lua_hud_lifecycle(self):
        try:
            from lupa import LuaRuntime
        except ImportError:
            self.skipTest('Optional Lua runtime not installed')
        lua=LuaRuntime()
        lua.execute('''
          script={}; captured=0; stamp=100; pos=.5; pit=false; native=0; track='test/layout'
          flags={source='live', sent_at=100, track_id='test/layout',ranges={{.3,.55}}}
          ac={FolderID={Root=0}, FlagType={None=0,Caution=2},
            getFolder=function() return 'root' end,onRelease=function() end,
            getTrackFullID=function() return track end,
            getSim=function() return {raceFlagType=native} end,
            getCar=function() return {splinePosition=pos,isInPit=pit,isInPitlane=false} end}
          ui={drawRaceFlag=function() captured=captured+1 end}
          os.time=function() return stamp end
          io.load=function(path) return path end
          JSON={parse=function(path) if string.find(path,'marshal_cautions') then return flags else return {} end end}
        ''')
        lua.execute(Path('ac_apps/marshal_drone_cams/marshal_drone_cams.lua').read_text())
        lua.execute('script.update(.1); script.drawCautionHUD(); assert(captured==1)')
        for condition in ('pos=.8','pos=.5; stamp=103','stamp=100; pit=true',
                          'pit=false; native=5','native=0; track="other"'):
            lua.execute(condition+'; script.drawCautionHUD(); assert(captured==1)')
        lua.execute('track="test/layout"; flags.ranges={}; script.update(.1); script.drawCautionHUD(); assert(captured==1)')
