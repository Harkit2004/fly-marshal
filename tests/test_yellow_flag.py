from pathlib import Path
import unittest


class NativeFlagTests(unittest.TestCase):
    def test_override_and_release_lifecycle(self):
        from lupa import LuaRuntime
        lua = LuaRuntime()
        lua.execute('''
          now=100; flag=12; allowed=true; pos=.5; pit=false
          sim={}; track='test'
          ac={FlagType={None=0,Caution=2},getSim=function() return sim end,
            getTrackFullID=function() return track end,
            getCar=function() return {splinePosition=pos,isInPit=pit} end}
          physics={allowed=function() return allowed end,
            overrideRacingFlag=function(v) flag=v end}
          os.time=function() return now end
          packet={source='live',sent_at=100,track_id='test',ranges={{.3,.6}}}
        ''')
        lua.globals().control = lua.execute(Path('ac_apps/marshal_drone_cams/yellow_flag.lua').read_text())
        # Override even an existing flag, then release for each invalidating condition.
        for change, restore in [
            ('pos=.8', 'pos=.5'), ('now=103', 'now=100'),
            ('pit=true', 'pit=false'), ('track="other"', 'track="test"'),
            ('sim.isOnlineRace=true', 'sim.isOnlineRace=false'),
            ('sim.isReplayActive=true', 'sim.isReplayActive=false'),
            ('packet.native_enabled=false', 'packet.native_enabled=true'),
            ('control.enabled=false', 'control.enabled=true'),
            ('packet.ranges={}', 'packet.ranges={{.3,.6}}'),
        ]:
            lua.execute('control.update(packet); assert(flag==2 and control.active)')
            lua.execute(change+'; control.update(packet); assert(flag==0 and not control.active); '+restore)
        lua.execute('control.update(packet); control.reset(); assert(flag==0 and not control.active)')
        lua.execute('allowed=false; control.update(packet); assert(not control.active)')
        lua.execute('allowed=true; physics.overrideRacingFlag=function() error("denied") end; control.update(packet); assert(not control.active)')
