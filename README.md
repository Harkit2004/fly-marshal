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
| `cv/report.py`, `cv/worker.py` | Teammate | Telemetry reports and asynchronous OpenAI vision on fresh AC game frames |
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

## Incident models (trained)

Trained on the six Vallelunga sessions (Audi Quattro rally, one class): clean, spin, offs, stopped,
contact, rejoin/limp. Tested on two sessions it never trained on: **Spa with a GT3 grid** and
**Spa with F1 cars** (different track, different car class, 15 vs 20 Hz).

```bash
python -m pipeline.vrclog_adapter data/raw/vallelunga/spin.txt --name vl_spin     # (each log)
python -m ml.dataset data/sessions/vl_* data/sessions/spa_gt3_demo data/sessions/spa_example
python -m ml.train --train vl_clean vl_spin vl_offs vl_stopped vl_contact vl_rejoin_limp \
                   --test spa_gt3_demo spa_example --candidates lightgbm logistic --trials 5
python tools/compare_models.py --train vl_clean vl_spin vl_offs vl_stopped vl_contact vl_rejoin_limp \
                   --test spa_gt3_demo spa_example
```

- **Detector** (what is happening now: spin / off / slide / stopped / contact): 50/50 blend of tuned LightGBM and logistic regression.
- **Predictor** (will this car have an incident in the next 5 s): blend of the same two.
- Chosen by leave-one-session-out cross-validation over LightGBM, HistGradientBoosting, random forest and logistic regression, then tuned. Alarm thresholds come from out-of-fold predictions with the brain's own alarm logic.
- Features are all relative to what is normal at that point of that track (speed / typical speed, yaw beyond what the corner needs, acceleration beyond typical, gaps in seconds). Raw km/h, metres and G didn't transfer: the first model gave 3–10 false alarms/min on Spa.
- Trains in ~40 min on a 12-core laptop CPU. Colab isn't needed (its free CPU is slower and a GPU doesn't help at this size).

End to end through the real brain (`tools/compare_models.py`), incidents incl. hard contacts (≥15 km/h relative):

| | Rules | Trained model |
|---|---|---|
| **Held-out Spa (GT3 + F1)**: detected | 11 / 47 (23%) | **21 / 47 (45%)** |
| held-out: false alarms | 0.51 / min | 0.83 / min |
| held-out: spins + offs | 10 / 11 | **11 / 11** |
| held-out: contacts | 0 / 25 | 9 / 25 |
| Vallelunga, cross-validated (honest) | – | 61% at 0.27 false alarms / min |
| Vallelunga, final model (optimistic, it trained on these) | 62 / 122 | 109 / 122 |
| Median detection latency | 0.33 s | 0.36 s |

Weak spots: minor slides (1/8 on held-out), stopped cars on Spa F1 (0/3, rules 1/3), and prediction in general
(warns before ~5–10% of incidents, ~1.4 s ahead). Most incidents here were player mistakes with little warning in the
telemetry. Caveat: the Spa sessions were looked at once to diagnose the unit problem above, so they are not a
perfectly untouched test. Record a fresh session on a new track for a clean final check.

Full numbers: `reports/training/report.json`, `reports/e2e/`.

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

## Game-rendered drone feeds (issue #1)

Implementation/verification checklist: [ISSUE_1_CHECKLIST.md](ISSUE_1_CHECKLIST.md).
These are **simulated drone cameras rendered inside Assetto Corsa**, not physical drone feeds.
The Three.js scene remains the overview and replay fallback. Its images are never sent to vision.

1. Install dependencies: `python -m pip install -r requirements.txt`.
2. Set `AC_ROOT` in your local `.env` (for example `AC_ROOT=B:/SteamLibrary/steamapps/common/assettocorsa`).
3. Run `python tools/install_game_cams.py`. This installs only the new app, backing up changed app files.
4. Restart the AC session and open **Marshal Drone Cams** in the Lua apps taskbar. Enable the VRC logger and start a race.
5. Run `python tools/run_demo.py --live --game-feeds`, then open <http://localhost:8000>.
   Alternatively set `game_feeds.enabled = true` and use the original three-terminal commands.
   `--game-feeds` enables captures for that launch without editing the shared settings.
6. Fresh tiles say **GAME**. After two seconds without a fresh capture, they revert to **3D**.
   Replay and `live_bridge --logs-dir ...` simulations always use 3D, even if AC is open.

The bridge atomically publishes `<AC>/logs/marshal_poses.json` at up to 10 Hz. The app polls it and
stagger-renders three 640×360 cameras at a target of four frames/sec each. `pose_port` is reserved;
this implementation deliberately uses the issue's permitted JSON-file transport, not UDP.
Each capture has a run/event identifier and an immutable JPEG filename referenced by its metadata.
`drone_<id>.jpg` also contains the latest complete JPEG. Old sessions and partial/stale files are rejected.
During an incident the app aims at the current in-game car position, compensating for logger buffering.
Camera capture continues between logger flushes; drone movement still follows buffered telemetry.
After 15 seconds without telemetry the brain stops camera requests. Use the logger's recommended
one-second flush interval for a responsive live demo. Exported images use CSP's main shaders and
YEBIS tone mapping so scene lighting is converted correctly to JPEG.

**Rendering check required:** CSP [issue #629](https://github.com/ac-custom-shaders-patch/acc-extension-config/issues/629)
reports missing distant car bodies in GeometryShot. Inspect the incident car yourself; a fresh JPEG does not prove
that all geometry rendered. Smoke/particles may also differ from the main view. If needed, set
`game_feeds.method = "main_camera"` and `selected_drone` in settings, restart the brain, and check
**Allow main-camera takeover** in the app. This captures one drone using AC's actual screen resolution,
including HUD; it takes over your view and is intended for spectating. Other tiles fall back to 3D.
Disable the checkbox to release the camera. Lost/stale poses also release it automatically.

**Vision:** Put `OPENAI_API_KEY=...` in `.env` locally (never in git or a chat), set `vision.enabled = true`,
and restart the brain. This implementation supports `provider = "openai"`; other providers fail closed.
The default [GPT-6 Luna model](https://developers.openai.com/api/docs/models/gpt-6-luna) supports image input
and structured outputs. Requests use the [Responses image-input format](https://developers.openai.com/api/docs/guides/images-vision).
One fresh game frame is assessed when a drone is within 8 m of its assigned incident station.
A single image generally cannot establish motion: uncertain fields stay null/`?`, including stopped.
Driver injuries are not inferred. The API call is bounded by `vision.timeout_s`; failures leave the
telemetry report in place and cannot block the control loop. Only one request can be in flight.
Results are cached by event with image hash, capture run and model, preventing cross-race cache reuse.
API usage incurs the configured provider's charges.

**Live acceptance:** Record the app's FPS with capture paused for 30 seconds, then enabled for 30 seconds,
using the same race/camera/settings. Confirm all three tiles, distant incident-car visibility and report
updates. `<frames_dir>/status.json` records current game FPS and last capture duration; no performance
claim has been verified until this comparison is performed. Changing weather/traffic can affect FPS.

**Replay acceptance:** Close AC, run `python tools/make_synthetic.py`, then
`python tools/run_demo.py`. All feeds should say 3D, and debris/smoke/driver-out/blocking remain `?`.
Component logs are in `data/runtime/`; Ctrl+C stops the combined launcher and its children.

**Changing tracks/cars:** Keep the launcher running. The bridge watches the logger's active-session
pointer and clears the old cars, alerts, reports, drone state and camera frames when it changes.
It reads the new track's AI line from the first telemetry sample, including stationary cars.
Deleted/finalized part files and temporarily empty pointers are retried automatically.
The first data still depends on the logger flush interval (default 10 seconds; use 1 for live demos)
and configured playback delay. Tracks without an AI line need first-lap coverage before their map appears.
An explicit reference session is used only when its track ID matches the current track.

**Performance settings:** The corrected fly model uses CSR connectivity and avoids boolean-index
GPU synchronization. Neuron count, connections and 20 simulation substeps are unchanged.
`flybrain.max_hz = 15` caps controller work and keeps only the latest requested input.
Incident cameras retain `game_feeds.fps = 4` at 640×360; quiet patrol cameras use
`game_feeds.patrol_fps = 2`. Thus three patrol cameras request six captures/second instead of twelve.
JPEG generations are cached while fresh; age/session checks still run on every read.
`scene.dashboard_fps = 30` caps browser rendering (set 60 for a smoother display at higher GPU cost).
Sidebar changes are batched at 10 Hz; hidden pages skip drawing. Detection and telemetry rates are unchanged.

To reproduce the neural performance/equivalence check, stop the launcher and run
`python tools/benchmark_flybrain.py`, then restart normally. This laptop measured 23.1 → 14.3 ms
per neural compute step (1.61×), with equal motor/spike outputs across the benchmark inputs.
This is a compute benchmark, not a measured increase in AC game FPS; its report is saved in
`data/runtime/fly-performance.json`. Live FPS depends on race, camera and graphics settings.

**Automated checks:** `python -m unittest discover -s tests -v` tests transport provenance, staleness,
replay isolation, pose targeting, strict report parsing, caching and a nonblocking timeout using a mock API.
These checks require no API key and do not establish GPU performance or live rendering quality.

## Coordinates

### Yellow caution zones (display only)

Active incidents, including slow-car escorts, colour the 2D track and minimap yellow from
200 m before to 50 m after their current track position. Predictions alone do not declare
a yellow zone. Overlaps merge and the start/finish boundary wraps; zones disappear when
their incidents end. This is a demo visualization, not an official marshal-sector system.
Configure `scene.yellow_before_m` and `scene.yellow_after_m`, or set
`scene.yellow_zones_enabled = false` to disable it; restart the bridge and refresh the page.
No AI controls or native game flags are changed by this feature.

For a future 3D view, use a translucent yellow ribbon beside the road with entry/exit markers,
keeping the road and cars visible. The minimap already displays the same yellow zones.
The installed CSP 0.2.11 SDK exposes `ui.drawRaceFlag(ac.FlagType.Caution)` for a flag in
the normal HUD position and `physics.overrideRacingFlag(...)` for native flag override.
The SDK requires restoring `ac.FlagType.None` to release that override. Neither API has
been enabled or live-tested here. Prefer display-only first; native override needs session,
timeout and unload cleanup and must not mask more important game flags.

Live response now uses one drone per car, including retries and warning-to-incident upgrades.
Warning scores are rechecked while active; alert coordinates and drone targets follow current telemetry.
Recovered moving cars clear after `detection.clear_after_s` (0.75 s), including departure beyond
`detection.departed_m` (40 m), with a short rearm delay to avoid repeated alerts from stale feature windows.
Stationary hazards remain active. Closed alerts also clear their dashboard report.
Slow-car escorting supplements the crash ML model with a telemetry rule: sustained running
between 5 and 100 km/h, over 60 km/h below the expected speed at that track location, for
2 seconds without heavy braking. Pit cars and cars with three or more wheels off track
are excluded. The `detection.slow_*` settings control these limits. A single drone follows
the car until its speed deficit stays below 30 km/h for the recovery interval, it pits,
or disappears; leaving the initial incident location does not cancel the escort.
Live consumers coalesce queued frames rather than processing a backlog; replay retains every frame.
The logger still adds its configured flush latency (1 s locally) plus `live.delay_s`.
Drone speed is now a simulation setting of 130 m/s (468 km/h), with 50 m/s² acceleration;
actual speed is lower when turning, braking or holding. These are not physical drone specifications.

AC world coordinates, metres, `y` up. `track_pos` is AC's spline position (0–1). If the 3D view looks mirrored compared with the 2D map, set `MIRROR_Z = true` in `dashboard/view3d.js`.

## Sponsor logos

See `dashboard/assets/sponsors/README.md`. Drop in logo files and list them in `sponsors.json`.
