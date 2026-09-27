"""Convert local AC diffuse skin overrides to browser PNGs (incrementally cached)."""
import configparser
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.settings import ROOT, get


def prepare(ac_root=None, output=None):
    from PIL import Image
    car_id = 'tatuusfa1'
    root = Path(ac_root or get('live.ac_root', env='AC_ROOT')) / 'content/cars' / car_id
    ini_path = root / 'unpacked-tatuusfa1/Tatus_Abarth_LOD_0.fbx.ini'
    if not ini_path.exists():
        print('[skins] Unpacked Tatuus material map missing; using base textures.')
        return None
    dest = Path(output or ROOT / 'dashboard/assets/models/skins/tatuusfa1')
    dest.mkdir(parents=True, exist_ok=True)
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(ini_path)
    diffuse = {}
    for section in ini.sections():
        data = ini[section]
        if not section.startswith('MATERIAL_') or 'NAME' not in data:
            continue
        for i in range(int(data.get('RESCOUNT', 0))):
            if data.get(f'RES_{i}_NAME') == 'txDiffuse':
                diffuse[data['NAME']] = data[f'RES_{i}_TEXTURE'].lower()
    skins = {}
    for folder in sorted((root / 'skins').iterdir()):
        if not folder.is_dir():
            continue
        files = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
        maps = {}
        for material, filename in diffuse.items():
            src = files.get(filename)
            if src is None:
                continue  # AC inherits the base texture for absent overrides.
            version = f'{folder.name}/{filename}/{src.stat().st_mtime_ns}/{src.stat().st_size}/v1'
            name = hashlib.sha256(version.encode()).hexdigest()[:24] + '.png'
            target = dest / name
            if not target.exists():
                with Image.open(src) as image:
                    image = image.convert('RGBA')
                    image.thumbnail((2048, 2048))
                    image.save(target)
            maps[material] = name
        skins[folder.name] = maps
    manifest = dest / 'manifest.json'
    temporary = dest / 'manifest.tmp'
    temporary.write_text(json.dumps(dict(car_model=car_id, skins=skins), indent=2), encoding='utf-8')
    temporary.replace(manifest)
    print(f'[skins] Prepared {len(skins)} Tatuus liveries.')
    return manifest


if __name__ == '__main__':
    prepare()
