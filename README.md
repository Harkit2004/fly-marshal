# Marshal Fly

Track 1 · Safety Diagnosis. Telemetry spots a developing hazard or an incident on track, the nearest drone goes there, and race control gets a live view and an incident report. One of the drones is flown by a fruit-fly connectome. The pitch: low-cost safety coverage for club circuits and long tracks that can't afford cameras and marshals everywhere.

```
AC + VRC Race Logger ──► vrclog_adapter ──► session folder ──► replay_stream ──ws:8765──► brain.py ──ws:8766──► dashboard
   (teammate)             (reuses the logger's      │                              features → detectors → events
                           parser + detectors)      └──► process ──► ML training      → dispatcher → pilots (fly / PID)
                                                                                      → safety layer → drone sim
```

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

## Layout and owners

| Path | Owner | What |
|---|---|---|
| `third_party/assetto-corsa-race-logger/app/vrc_race_logger` | Teammate | In-game logger. Copy to `<AC>/apps/lua/`, needs Custom Shaders Patch. Logs go to `<AC>/logs/vrclog_*.txt` |
| `pipeline/vrclog_adapter.py` | Teammate | `vrclog_*.txt` → `data/sessions/<name>/` (telemetry, events, centreline, meta) |
| `pipeline/process.py` | Teammate | Session → training table with features and `incident_in_5s` labels (`data/processed/`) |
| `pipeline/replay_stream.py` | Teammate | Streams a session over websocket in real time (the demo runs on this) |
| `cv/report.py` | Teammate | Incident reports. Telemetry version works; vision-model version is a TODO |
| `dashboard/` | Teammate | 2D race-control map + Three.js 3D view with sponsor boards |
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

## FlyBrain setup

The connectome CSVs are not in the flybrain repo. Put `fly_neurons_real.csv` and `fly_synapses_real.csv` (built from FlyWire FAFB v783, see `third_party/flybrain/README.md`) in a folder, then:

```bash
set FLYBRAIN_DATA=D:\flybrain_data
set FLYBRAIN_SYNAPSE_STRIDE=4        # optional, if 8 GB VRAM is tight
python brain.py --steer-assist 0.0   # raise it only if the real fly can't turn toward targets
```

Without the data, drone 0 uses a placeholder "fly" with the same interface, so everything else still runs.

## Coordinates

AC world coordinates, metres, `y` up. `track_pos` is AC's spline position (0–1). If the 3D view looks mirrored compared with the 2D map, set `MIRROR_Z = true` in `dashboard/view3d.js`.

## Sponsor logos

See `dashboard/assets/sponsors/README.md`. Drop in logo files and list them in `sponsors.json`.
