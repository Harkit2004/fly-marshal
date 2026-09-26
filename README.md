# Marshal Fly

Track 1 · Safety Diagnosis. Telemetry spots a developing hazard or an incident on track, the nearest drone goes there, and race control gets a live view and an incident report. One of the drones is flown by a fruit-fly connectome. The pitch: low-cost safety coverage for club circuits and long tracks that can't afford cameras and marshals everywhere.

```
                                   live:   live_bridge (tails the logger's chunk files) ─┐
AC + VRC Race Logger ─┤                                                                   ├─ws:8765─► brain.py ─ws:8766─► dashboard
                      └─ vrclog_adapter ─► session folder ─► replay_stream ──────────────┘   features → detectors → events
                                                 └─► process ─► ML training                  → dispatcher → pilots (fly / PID)
                                                                                             → safety layer → drone sim
```

Live and replay publish identical messages, so the brain and dashboard don't care which one is running.

## Built on

| Project | Used for | Where |
|---|---|---|
| [Zhaoyi-Fan/assetto-corsa-race-logger](https://github.com/Zhaoyi-Fan/assetto-corsa-race-logger) (MIT) | In-game logger (CSP Lua app, every car at 15 Hz + collision events), log parser, track model, incident detectors used as ground-truth labels | `third_party/assetto-corsa-race-logger` |
| [dylankainth/flybrain](https://github.com/dylankainth/flybrain) (MIT) | FlyWire connectome controller (`RealFlyBrain`) that flies drone 0 | `third_party/flybrain` |

Both are git submodules. Clone with:

```bash
git clone --recurse-submodules <repo-url>
```

or, in an existing clone:

```bash
git submodule update --init
```

## Settings

Everything you'd want to tune lives in **`settings.toml`** at the repo root, with a comment on every line: AC install path and live delay, where the live track shape comes from, detection thresholds, drone limits, versus mode, fly-brain model and synapse stride, the vision model, 3D scene sizes, and custom car/drone/track models. Python reads it directly. The dashboard receives its part inside the track message, so restart the replay or live bridge after changing `[scene]`, `[models]` or `[tracks]`.

## Quick start (no game needed)

```bash
pip install -r requirements.txt

# 1. data: a synthetic race, and/or the real Spa race log that ships with the logger repo
python tools/make_synthetic.py
unzip third_party/assetto-corsa-race-logger/examples/vrclog_spa_race_example.zip -d data/raw
python -m pipeline.vrclog_adapter data/raw/vrclog_20260803_231630_spa-layout_f1_2025_race_r13.txt --name spa_example

# 2. run it live (three terminals)
python -m pipeline.replay_stream --session data/sessions/spa_example --start 40
python brain.py
python -m http.server 8000 --directory dashboard     # open http://localhost:8000

# 3. score detection against ground truth, offline
python tools/evaluate.py data/sessions/spa_example
```

## Live mode (race running in AC)

```bash
# set live.ac_root in settings.toml, start a session in AC with the VRC Race Logger app on, then:
python -m pipeline.live_bridge
python brain.py
python -m http.server 8000 --directory dashboard
```

No converted session is needed. The bridge reads the track name from the logger and builds the track itself:

1. `--reference data/sessions/<name>`, if you pass one (best typical speeds, from real laps).
2. Otherwise the track's AI line, `<AC>/content/tracks/<track>/<layout>/ai/fast_lane.ai`: exact shape, widths and AI target speeds.
3. Otherwise (a custom track with no AI line) it learns the shape from the first lap: drive one clean lap and the track appears on the dashboard. Nothing is streamed until then.

### Our own track

1. Build or download the track and install it in `<AC>/content/tracks/` (a track made in Blender goes through Kunos' ksEditor to a `.kn5`; RTB (Race Track Builder) exports straight to AC).
2. Record an AI line in AC (drive clean laps with the AI line recorder, or use CSP's) so `fast_lane.ai` exists. Without one, the first-lap fallback still works.
3. Optional look: export the track model to `.glb` (Content Manager → *Unpack KN5* → FBX → Blender → glTF), put it in `dashboard/assets/models/` and add a `[tracks."<track>/<layout>"]` entry in `settings.toml`.
4. Race it with the logger on, and run the three commands above.

The logger writes a chunk at least every 10 s (`FLUSH_SECONDS` in `vrc_race_logger.lua`), so live data runs 10–12 s behind the game. Set `FLUSH_SECONDS = 1` in your installed copy of the app (not in `third_party/`) for about 2–3 s. To test live mode without the game:

```bash
python tools/simulate_live_logger.py data/raw/vrclog_...txt --logs data/live_logs --flush 2
python -m pipeline.live_bridge --logs-dir data/live_logs                  # learns the track from the first lap
```

## Dashboard

- **2D map**: race-control overview with alerts, incident card and drone list.
- **3D view**: track with kerbs, run-off, sponsor boards, gantry, grandstand and trees; animated cars and drones, interpolated between ticks.
  - Cameras: **Orbit**, **Chase** (orbit around the selected drone), **Drone cam** (the drone's gimbal camera with HUD; drag = pan/tilt, wheel = zoom, double-click = auto-track), **TV cam** (nearest trackside camera zooms on the action), **Incident**.
  - Live **feed tiles** for every drone; click one to fly its camera. Keys: `1`–`9` select a drone, `c` cycles cameras.
  - Minimap with the camera's position and heading.
- **Fly brain panel**: 20,073 real FlyWire neurons at their real coordinates inside the FlyWire brain mesh. A neuron flashes only when that neuron spiked in the connectome simulation; with the placeholder pilot the brain stays dark.
- **Incident card**: what telemetry knows (moving/stationary, on/off line, next car's arrival). Debris and smoke show as "?" until the vision model has looked at the drone frame.
- **Custom models**: glTF cars, drones and per-track models via `settings.toml` (see `dashboard/assets/models/README.md`).

## Layout and owners

| Path | Owner | What |
|---|---|---|
| `third_party/assetto-corsa-race-logger/app/vrc_race_logger` | Teammate | In-game logger. Copy to `<AC>/apps/lua/`, needs Custom Shaders Patch. Logs go to `<AC>/logs/vrclog_*.txt` |
| `pipeline/vrclog_adapter.py` | Teammate | `vrclog_*.txt` → `data/sessions/<name>/` (telemetry, events, centreline, meta) |
| `pipeline/process.py` | Teammate | Session → training table with features and `incident_in_5s` labels (`data/processed/`) |
| `pipeline/replay_stream.py` | Teammate | Streams a session over websocket in real time (the demo runs on this) |
| `pipeline/live_bridge.py` | Teammate | Streams the race running in AC right now, from the logger's chunk files |
| `pipeline/track_source.py` | Teammate | Live track shape from the AI line or the first lap |
| `tools/simulate_live_logger.py` | Teammate | Fakes the logger mid-race from an old log, for testing live mode |
| `cv/report.py` | Teammate | Incident reports. Telemetry version works; vision-model version is a TODO (`settings.toml [vision]`) |
| `dashboard/` | Teammate | 2D map, 3D view (cameras, drone feeds, sponsor boards), fly-brain viewer, custom model hooks |
| `ml/features.py`, `ml/detectors.py` | You | Online features; anomaly detector and risk predictor (rule baselines, swap in trained models) |
| `drones/` | You | Sim, PID and FlyBrain pilots, safety layer, dispatcher |
| `drones/flybrain_real.py` | You | Loads the real connectome from `third_party/flybrain` |
| `brain.py` | You | Ties ML and drones together |
| `tools/evaluate.py` | You | Offline scoring: detection rate, latency, false alarms, drone arrival |
| `shared/` | Both | Message formats, config, track geometry. Change only together |
| `settings.toml` | Both | Every tunable, in plain language |

## Session folder format

`data/sessions/<name>/`:

- `telemetry.csv`: one row per car per tick. Columns are in `shared/schemas.py` (`TELEMETRY_COLUMNS`).
- `events.csv`: ground truth. Loss-of-control episodes from the logger's analyzer (spin, slide, off, stuck, DNF), car-to-car contacts from its collision events, and retirements.
- `centerline.csv`: `track_pos, x, y, z, typical_speed_kmh`. From `fast_lane.ai` when `AC_ROOT` points at your AC install, otherwise averaged from car positions.
- `meta.json`

## Current baseline (rule-based, before any ML)

| Session | Incidents detected | Median latency | False alarms |
|---|---|---|---|
| synthetic_00 | 2 / 2 | 1.8 s | 0 |
| spa_example (real, 15 cars, 10 min) | 6 / 13 (misses minor slides) | 0.36 s | 0.1 / min |

Beating this with the trained models is the ML goal.

## FlyBrain setup (real FlyWire data)

Everything comes from FlyWire's public v783 release. No login is needed.

```bash
python tools/fetch_flywire.py              # ~10 MB: neurons, cell types, sides, coordinates + brain mesh -> 3D viewer works
python tools/fetch_flywire.py --synapses   # + 2.7 GB per-synapse table -> the connectome can run
python brain.py                            # data dir, model and synapse stride: settings.toml [flybrain]
```

- `fly_neurons_real.csv` is ordered left side first. RealFlyBrain splits its photoreceptor and motion pools into halves by index, so with this order its "left/right" becomes anatomical (about 95% exact for R1-6: 4,425 left vs 4,031 right) instead of the arbitrary split its README warns about.
- The 3D viewer shows 20,073 real FlyWire neurons at their real coordinates inside the FlyWire brain mesh, coloured by FlyWire super_class. A neuron flashes only when that exact neuron (same root id) spiked in the simulation. This is checked for every viewer neuron.
- Without the synapse table, drone 0 flies with a placeholder steering reflex. The viewer then shows the brain dark and says no neural activity is shown. Nothing is simulated or invented.
- The FlyWire data and the mesh (navis-flybrains, GPL-3.0) are downloaded, not committed.

### Findings from running the real connectome (stride 8, i7-1255U CPU)

| | Result |
|---|---|
| Load | 80.2M synapse rows → 10.0M edges (stride 8) → 5.77M connections, 126 s first time, then cached (`edges_stride8.npz`); ~0.8 GB RAM |
| Speed | 4.4 s per control step (20 × 1 ms substeps) on CPU. Needs the RTX 3070 for anything near real time |
| Upstream `RealFlyBrain` as-is | Silent with no input; **any** input drives it into the same saturated state (~32% of all neurons spiking every ms, matching its README's "35,000+ neurons per timestep"). Left, right and neutral targets give **identical** activity, and the turn output sits at −1.0, so it cannot steer. |
| Likely cause | `tau_m = R_m * C_m = 10 × 2e-6 = 20 µs` with a 1 ms Euler step (50× too large): any small input is amplified ~49× per step until it spikes. Shiu et al. 2024 use τ_m = 20 ms. |
| With τ_m = 20 ms (experiment) | Sparse, input-dependent activity (~6% active). Left target: left photoreceptors 167 Hz vs right 33 Hz; right target mirrored. Activity does not reach T4/T5 motion neurons or descending neurons with a constant optic-flow drive. |

Takeaway: the real brain runs and its spikes are real, but connectome-only steering is not supported by the current model.

**What runs now (default `FLYBRAIN_MODEL=corrected`):** `CorrectedLIF` in `drones/flybrain_real.py` uses the same connectome and neuron pools with τm = 20 ms. Measured: target left → turn +0.50, right → −0.50, ahead → 0.00. About 850 spikes/ms instead of ~44,000. Heading is read from the simulated photoreceptors' left/right firing. Descending neurons stay silent with this input, so forward speed and altitude are flown by plain beacon control, and the dashboard labels them "assisted". `FLYBRAIN_MODEL=upstream` runs the original for comparison.

Pitch line: "A real FlyWire fly brain runs live and sees which side the incident is on; the drone's heading follows its photoreceptors, speed and altitude are assisted."

## Coordinates

AC world coordinates, metres, `y` up. `track_pos` is AC's spline position (0–1). If the 3D view looks mirrored compared with the 2D map, set `MIRROR_Z = true` in `dashboard/view3d.js`.

## Sponsor logos

See `dashboard/assets/sponsors/README.md`. Drop in logo files and list them in `sponsors.json`.
