from pathlib import Path
import unittest


class YellowAITests(unittest.TestCase):
    def setUp(self):
        try:
            from lupa import LuaRuntime
        except ImportError:
            self.skipTest('Optional Lua runtime unavailable')
        self.lua=LuaRuntime()
        self.lua.execute('''
          now=100; allowed=true; limits={}; aggression={}
          cars={
            [0]={isAIControlled=false,splinePosition=.1,speedKmh=100,aiAggression=.5},
            [1]={isAIControlled=true,splinePosition=.45,speedKmh=100,aiAggression=.7},
            [2]={isAIControlled=true,splinePosition=.46,speedKmh=40,aiAggression=.8}}
          sim={carsCount=3,trackLengthM=1000}
          ac={getSim=function() return sim end,getCar=function(i) return cars[i] end,
            getTrackFullID=function() return 'track' end}
          physics={allowed=function() return allowed end,
            setAITopSpeed=function(i,v) limits[i]=v end,
            setAIAggression=function(i,v) aggression[i]=v end}
          os.time=function() return now end
          packet={source='live',ai_enabled=true,sent_at=100,track_id='track',session_id='run',
            ranges={{.3,.6}},speed_kmh=80,approach_m=100,gap_m=20,incident_cars={}}
        ''')
        self.lua.globals().control=self.lua.execute(Path('ac_apps/marshal_drone_cams/yellow_control.lua').read_text())

    def test_cap_following_player_exclusion_and_exit_restore(self):
        self.lua.execute('''
          control.update(packet,.1)
          assert(limits[0]==nil and limits[1]<40 and limits[2]==80)
          assert(aggression[1]==0 and control.owned[1].leader==2)
          cars[1].splinePosition=.7; control.update(packet,.1)
          assert(limits[1]==1e9 and aggression[1]==.7 and control.owned[1]==nil)
        ''')

    def test_stale_offline_permissions_and_shutdown(self):
        self.lua.execute('''
          control.update(packet,.1); now=103; control.update(packet,.1)
          assert(next(control.owned)==nil and limits[2]==1e9)
          now=100; allowed=false; control.update(packet,.1); assert(next(control.owned)==nil)
          allowed=true; sim.isOnlineRace=true; control.update(packet,.1); assert(next(control.owned)==nil)
          sim.isOnlineRace=false; control.update(packet,.1); control.reset()
          assert(next(control.owned)==nil and aggression[2]==.8)
        ''')

    def test_pits_hazard_exclusion_and_approach(self):
        self.lua.execute('''
          cars[1].splinePosition=.25; cars[2].isInPitlane=true
          control.update(packet,.1); assert(limits[1]>80 and limits[1]<150 and limits[2]==nil)
          packet.incident_cars={1}; control.update(packet,.1)
          assert(limits[1]==1e9 and next(control.owned)==nil)
        ''')
