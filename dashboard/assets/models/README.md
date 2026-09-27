# Custom 3D models

The 3D view draws procedural cars, drones and tracks by default. To use real models, put glTF files (`.glb`) here and point to them in **`settings.toml`** at the repo root (`[models.car]`, `[models.drone]`, `[tracks."<track id>"]`). Restart the replay / live bridge (it sends these settings to the dashboard), then reload the page.

```toml
[models.car]
file = "gt3.glb"
scale = 1.0
rotation_y = 1.5708

[tracks."spa/layout_f1_2025"]
file = "spa.glb"
offset = [0.0, 0.0, 0.0]
hide_procedural = true
```

## Cars

The local demo uses `tatuus_fa01.glb`, converted from the installed AC Tatuus FA01.
It has embedded diffuse textures, +X forward orientation, ground-level tyres and
four wheel pivots. Duplicate low-resolution cockpit and blurred wheel meshes are
removed; body meshes are joined to reduce draw calls. Each Tatuus now uses its recorded
skin ID. The live logger's `skin` field is forwarded to the viewer; new converted replay
CSVs preserve it too. Older CSVs without that field retain the base textures.
The launcher incrementally converts installed diffuse skin overrides to local PNGs with
`tools/prepare_car_skins.py`. It matches original AC material texture filenames, including
body paint, logos and cockpit protection. Shared geometry stays unchanged; materials are
cached per skin and assigned separately to each car. Missing overrides inherit the GLB's
base texture. Other car models never receive a Tatuus skin just because a skin name matches.
Skin-specific shader effects and CSP procedural paint are not reproduced.

`[models.car].skin_manifest = "skins/tatuusfa1/manifest.json"` enables the mapping.
Remove that setting to return to one base livery. Restart the launcher and refresh the
dashboard after installing/changing skins. Converted images remain local and ignored by
Git. Native AC files and the logger are not modified.
Set `[models.car].file = ""` to restore the procedural car and restart the launcher.
The GLB is local and ignored by Git. Rebuild with Blender 4.2:

```powershell
& 'C:/Program Files/Blender Foundation/Blender 4.2/blender.exe' --background --factory-startup --python tools/convert_tatuus.py -- 'B:/SteamLibrary/steamapps/common/assettocorsa/content/cars/tatuusfa1/unpacked-tatuusfa1/Tatus_Abarth_LOD_0.fbx' dashboard/assets/models/tatuus_fa01.glb
```

- Forward must be **+X** after `rotation_y` (radians). The model is multiplied by `scene.car_scale` (settings.toml), so build it at real size in metres.
- Meshes whose names contain `wheel`, `tyre` or `tire` spin with the car's speed.
- Every car gets the same model. Recorded Tatuus skins apply to matching Tatuus entries;
  the procedural fallback retains its generated liveries and sponsor decals.

## Drones

- Nodes matching `rotor_nodes` (a regex) spin about their local Y axis. Embedded animations in the file play on loop.
- The model is multiplied by `scene.drone_scale` (3× by default) so drones stay visible at track scale.

## Tracks

- The key is the track id from the session's `meta.json` (`track`, e.g. `spa/layout_f1_2025`); the dashboard receives it in the track message.
- The model must be in **AC world coordinates** (metres, y up) so cars line up. `offset` / `rotation_y` / `scale` are for fixing small misalignments.
- `hide_procedural: true` hides the generated grass, asphalt and edge lines. Sponsor boards, kerbs and the gantry stay, so you can check alignment.

### Getting an AC track into glTF

1. AC track models are `.kn5` files in `<AC>/content/tracks/<track>/`. Community kn5 converters and Blender kn5 importer add-ons can turn them into FBX/OBJ. Import into Blender and export as `.glb`, keeping the original coordinates (no recentring).
2. Only use track models you're allowed to use. Kunos and mod tracks are fine to show in a local demo, but don't commit or redistribute them. The `.gitignore` already skips `*.glb` in this folder.
3. Big tracks can be hundreds of MB. In Blender, delete the scenery you don't need and apply a Decimate modifier before exporting.

Without a model, any AC track still works: the procedural track is built from the centreline and half widths the adapter extracts from the track's AI line (`fast_lane.ai`).
