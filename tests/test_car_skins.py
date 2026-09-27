import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from PIL import Image
from pipeline.replay_stream import load
from shared.schemas import FRAME_FIELDS
from tools.prepare_car_skins import prepare


class CarSkinTests(unittest.TestCase):
    def test_matching_materials_and_incremental_export(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            car = root / 'content/cars/tatuusfa1'
            unpacked = car / 'unpacked-tatuusfa1'
            unpacked.mkdir(parents=True)
            (unpacked/'Tatus_Abarth_LOD_0.fbx.ini').write_text(
                '[MATERIAL_0]\nNAME=RT_Skin\nRESCOUNT=1\nRES_0_NAME=txDiffuse\nRES_0_TEXTURE=SkinBase.dds\n')
            for name, color in [('red',(255,0,0)),('blue',(0,0,255))]:
                skin=car/'skins'/name; skin.mkdir(parents=True)
                Image.new('RGB',(8,8),color).save(skin/'skinbase.dds')
            dest=root/'out'
            manifest=json.loads(prepare(root,dest).read_text())
            red=dest/manifest['skins']['red']['RT_Skin']
            blue=dest/manifest['skins']['blue']['RT_Skin']
            self.assertNotEqual(red,blue)
            self.assertEqual(Image.open(red).getpixel((0,0)),(255,0,0,255))
            stamp=red.stat().st_mtime_ns
            prepare(root,dest)
            self.assertEqual(red.stat().st_mtime_ns,stamp)

    def test_replay_preserves_skin_and_accepts_legacy_recordings(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            pd.DataFrame(dict(track_pos=[0,.5],x=[0,10],y=[0,0],z=[0,10],typical_speed_kmh=[100,100])).to_csv(root/'centerline.csv',index=False)
            frame=dict.fromkeys(FRAME_FIELDS,0)
            frame.update(t=1,driver='Driver',in_pit=False,car_model='tatuusfa1',skin='red')
            for include in (True,False):
                value=dict(frame)
                if not include: value.pop('skin'); value.pop('car_model')
                pd.DataFrame([value]).to_csv(root/'telemetry.csv',index=False)
                _,ticks=load(root)
                result=json.loads(ticks[0][1])['cars'][0]
                self.assertEqual(result.get('skin'), 'red' if include else None)
