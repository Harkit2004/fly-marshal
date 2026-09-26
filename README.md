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
# AC running a session with the VRC Race Logger installed; reference = a converted session of the same track
python -m pipeline.live_bridge --ac-root "D:/Steam/steamapps/common/assettocorsa" --reference data/sessions/our_track_clean_01
python brain.py
```

The logger writes a chunk at least every 10 s (`FLUSH_SECONDS` in `vrc_race_logger.lua`), so live data runs 10–12 s behind the game. Set `FLUSH_SECONDS = 1` in your installed copy of the app (not in `third_party/`) for about 2–3 s. To test live mode without the game:

```bash
python tools/simulate_live_logger.py data/raw/vrclog_...txt --logs data/live_logs --flush 2
python -m pipeline.live_bridge --logs-dir data/live_logs --reference data/sessions/spa_example
```

## Dashboard

- **2D map**: race-control overview with alerts, incident card and drone list.
- **3D view**: track with kerbs, run-off, sponsor boards, gantry, grandstand and trees; animated cars and drones, interpolated between ticks.
  - Cameras: **Orbit**, **Chase** (orbit around the selected drone), **Drone cam** (the drone's gimbal camera with HUD; drag = pan/tilt, wheel = zoom, double-click = auto-track), **TV cam** (nearest trackside camera zooms on the action), **Incident**.
  - Live **feed tiles** for every drone; click one to fly its camera. Keys: `1`–`9` select a drone, `c` cycles cameras.
  - Minimap with the camera's position and heading.
- **Fly brain panel**: 3D view of the fly brain. 250 sampled neurons per region (photoreceptors L/R, motion T4/T5 L/R, central brain, descending) flash when they fire. With the real connectome these are real spikes from `RealFlyBrain`; with the placeholder they are synthetic and labelled as such. The layout is schematic, not FlyWire coordinates.
- **Custom models**: glTF cars, drones and per-track models via `dashboard/assets/models/models.json` (see the README there).

## Layout and owners

| Path | Owner | What |
|---|---|---|
| `third_party/assetto-corsa-race-logger/app/vrc_race_logger` | Teammate | In-game logger. Copy to `<AC>/apps/lua/`, needs Custom Shaders Patch. Logs go to `<AC>/logs/vrclog_*.txt` |
| `pipeline/vrclog_adapter.py` | Teammate | `vrclog_*.txt` → `data/sessions/<name>/` (telemetry, events, centreline, meta) |
| `pipeline/process.py` | Teammate | Session → training table with features and `incident_in_5s` labels (`data/processed/`) |
| `pipeline/replay_stream.py` | Teammate | Streams a session over websocket in real time (the demo runs on this) |
| `pipeline/live_bridge.py` | Teammate | Streams the race running in AC right now, from the logger's chunk files |
| `tools/simulate_live_logger.py` | Teammate | Fakes the logger mid-race from an old log, for testing live mode |
| `cv/report.py` | Teammate | Incident reports. Telemetry version works; vision-model version is a TODO |
| `dashboard/` | Teammate | 2D map, 3D view (cameras, drone feeds, sponsor boards), fly-brain viewer, custom model hooks |
| `ml/features.py`, `ml/detectors.py` | You | Online features; anomaly detector and risk predictor (rule baselines, swap in trained models) |
| `drones/` | You | Sim, PID and FlyBrain pilots, safety layer, dispatcher |
| `drones/flybrain_real.py` | You | Loads the real connectome from `third_party/flybrain` |
| `brain.py` | You | Ties ML and drones together |
| `tools/evaluate.py` | You | Offline scoring: detection rate, latency, false alarms, drone arrival |
| `shared/` | Both | Message formats, config, track geometry. Change only together |

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
set FLYBRAIN_DATA=datalywire
set FLYBRAIN_SYNAPSE_STRIDE=2               # optional, if 8 GB VRAM is tight
python brain.py --steer-assist 0.0          # raise it only if the fly can't turn toward targets
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

Takeaway: the real brain runs and its spikes are real, but connectome-only steering is not supported by the current model. Steering uses `--steer-assist`. Say that plainly in the pitch.

## Coordinates

AC world coordinates, metres, `y` up. `track_pos` is AC's spline position (0–1). If the 3D view looks mirrored compared with the 2D map, set `MIRROR_Z = true` in `dashboard/view3d.js`.

## Sponsor logos

See `dashboard/assets/sponsors/README.md`. Drop in logo files and list them in `sponsors.json`.
