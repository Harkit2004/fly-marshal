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

- Forward must be **+X** after `rotation_y` (radians). The model is multiplied by `scene.car_scale` (settings.toml), so build it at real size in metres.
- Meshes whose names contain `wheel`, `tyre` or `tire` spin with the car's speed.
- Every car gets the same model. Liveries and sponsor decals only apply to the procedural car.

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
