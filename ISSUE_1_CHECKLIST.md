# Issue #1 — game-rendered drone feeds

Source: https://github.com/Harkit2004/fly-marshal/issues/1

Checked items mean implementation with the stated verification, not live acceptance.

## 1. CSP Lua camera app
- [x] Read atomic JSON poses at 10 Hz (the file alternative allowed by the issue).
- [x] GeometryShot per drone, sky, particles, transparency and clipping planes.
- [x] Confirm installed SDK supports `GeometryShot:save(path, ac.ImageFormat.JPG)` and `io.move`.
- [x] Publish JPEGs with temp/rename and generation-specific metadata.
- [x] Optional main-camera mode with explicit in-game takeover toggle and stale-pose release.
- [x] Install app on this computer; Lua syntax check passes.
- [ ] Verify incident cars render, including distant cars; compare main-camera fallback.
- [ ] Measure baseline and capture-enabled FPS with three cameras at 640×360.

## 2. Brain → AC
- [x] Publish world-space position, event/patrol look target and FOV, limited to 10 Hz.
- [x] Disabled by default; gated on live stream, never replay/simulated logger.
- [x] Pose coordinates and throttling tested.

## 3. Frames → dashboard
- [x] Send JPEG data URLs over the brain websocket.
- [x] GAME badge for fresh captures, 3D fallback after two seconds without a fresh image.
- [x] Reject stale, corrupt, wrong-session and wrong-source files in automated tests.
- [x] Observe all three GAME tiles in the live Spa race; sustained performance remains below.

## 4. Vision
- [x] Official GPT-6 Luna documentation confirms image input and structured outputs.
- [x] OpenAI Responses API, strict nullable JSON fields and image/model/session-aware cache.
- [x] Game-frame-only input; no browser/Three.js upload path.
- [x] Single background worker, timeout handling, actual on-station distance check.
- [x] Automated mock API/schema/cache/timeout tests.
- [ ] Real API call with user's key and an incident game frame.
- [ ] Observe incident card update within configured timeout.
- [x] Standalone real API test on a saved AC game image: valid structured response in 4.3 seconds. This does not verify live scheduling/card delivery.

## 5. Settings and documentation
- [x] Add game_feeds settings, installation helper and combined launcher.
- [x] Document live verification, replay fallback and known rendering limitations.

## Acceptance still requiring the game/user
- [x] Live AC race: all three feeds show GAME on Imola after automatic session reset.
- [x] Visual check: incident car is present in the live Spa drone-0 image after main-shader/YEBIS fix. Other distances still require testing.
- [ ] Real vision request and report displayed.
- [ ] FPS comparison recorded (do not infer performance from Python tests).
- [ ] AC-closed replay confirmed visually with 3D and unknown visual fields.
- [x] Replay visually verified: all three feeds show 3D and visual report fields stay unknown. AC process was still open, so the literal AC-closed check remains pending.

## Acceptance progress — 2026-09-26
- Camera-enabled run: 35 fresh status samples, mean 37.47 FPS, minimum sampled FPS 19.21. No matched capture-off sample: user left the race. This is not a measured camera FPS cost or a 1% low.
- Fixed Lua status updates during paused capture; installed with backup. Status now includes paused and poses_fresh.
- Saved camera views contain visible cars, but full distant-body visibility needs a matched main-camera comparison.
- Vision enabled locally in settings after key presence and configured-model access were verified without exposing the key.
- Player indicator implemented: green YOU marker drawn last on 2D/minimap, red downward cone and YOU label on player car 0 in 3D. Both visually checked in replay.
- JavaScript syntax checks and all 16 Python tests passed.

## Requested follow-up: start the real fly
- [x] Download FlyWire v783 dataset and generate viewer assets (139,255 neurons).
- [x] Install CUDA PyTorch 2.11.0+cu128 and verify RTX GPU support.
- [x] Verify real neuron spikes and left/right steering responses (tools/check_flybrain.py).
- [x] Start the live dashboard with the real fly controller; FlyWire source and neural update rate confirmed. Local stride 8 retains 5,773,733 connections; forward/climb assisted.

## Requested follow-up: automatic session switching
- [x] Detect active recording changes and finalized/deleted log parts without crashing.
- [x] Clear old cars, events, reports, drone controllers and game feeds; tag brain messages by session.
- [x] Load new AI-line track from first sample, including stationary cars; ignore mismatched references.
- [x] Validate new-session cleanup, partial files and stationary track loading: 14 automated tests pass in total.
- [x] Observe live Imola session transition and resumed three-camera dashboard without service restart.
- [x] Set installed logger flush interval to 1 second, preserving .before_live_flush.bak.
