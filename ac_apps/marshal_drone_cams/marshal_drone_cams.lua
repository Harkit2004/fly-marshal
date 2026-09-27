-- CSP 0.2.11 SDK: GeometryShot:save(path, ac.ImageFormat.JPG), io.move(..., true).
-- The fixed inbox allows frames_dir to be configured without modifying this app.
local inbox = ac.getFolder(ac.FolderID.Root) .. '/logs/marshal_poses.json'
local packet, camera = nil, nil
local shots, busy, previous = {}, {}, {}
local nextCapture = {}
local poll, statsClock, cursor, sequence = 0, 0, 0, 0
local paused, mainAllowed = false, false
local lastError, saved, captureMs = '', 0, 0
local mainId, cameraAge = nil, 0
local cautionInbox = ac.getFolder(ac.FolderID.Root) .. '/logs/marshal_cautions.json'
local cautionPacket, showCautions = nil, true

function script.drawCautionHUD()
  local p = cautionPacket
  if not showCautions or not p or p.source ~= 'live' or type(p.sent_at) ~= 'number'
      or math.abs(os.time() - p.sent_at) >= 2 or type(p.ranges) ~= 'table'
      or p.track_id ~= ac.getTrackFullID('/') then return end
  local car = ac.getCar(0)
  if not car or car.isInPit or car.isInPitlane then return end
  -- Leave AC's native flags visible: never override a black/blue/checkered flag.
  local native = ac.getSim().raceFlagType
  if native ~= ac.FlagType.None then return end
  local pos = car.splinePosition % 1
  for _, zone in ipairs(p.ranges) do
    if type(zone) == 'table' and type(zone[1]) == 'number' and type(zone[2]) == 'number'
        and pos >= zone[1] and pos <= zone[2] then
      ui.drawRaceFlag(ac.FlagType.Caution)
      return
    end
  end
end

local function releaseCamera()
  if camera then camera:dispose(); camera = nil end
  mainId, cameraAge = nil, 0
end

local function fresh()
  return packet and packet.source == 'live' and packet.enabled == true
    and type(packet.sent_at) == 'number' and math.abs(os.time() - packet.sent_at) < 2
    and type(packet.run_id) == 'string' and packet.run_id:match('^%x+$')
    and type(packet.frames_dir) == 'string' and type(packet.drones) == 'table'
end

local function vector(v)
  assert(type(v) == 'table' and #v == 3, 'Invalid camera vector')
  for i = 1, 3 do assert(type(v[i]) == 'number' and math.abs(v[i]) < 1e7, 'Invalid coordinate') end
  return vec3(v[1], v[2], v[3])
end

local function publish(p, d, temp, name, started)
  -- Metadata points to an immutable generation, avoiding JPEG/metadata races.
  if not fresh() or packet.run_id ~= p.run_id then return end
  local final = p.frames_dir .. '/' .. name
  assert(io.move(temp, final, true), 'Cannot publish JPEG')
  local bytes = io.load(final)
  assert(bytes and #bytes > 4, 'JPEG export returned no bytes')
  assert(io.save(p.frames_dir .. '/drone_' .. d.drone_id .. '.jpg', bytes, true), 'Cannot save latest JPEG')
  local meta = {source = 'game', run_id = p.run_id, drone_id = d.drone_id,
    event_id = d.event_id, filename = name, pose_seq = p.seq,
    captured_at = os.time(), method = p.method, capture_ms = (os.preciseClock() - started) * 1000}
  assert(io.save(p.frames_dir .. '/drone_' .. d.drone_id .. '.json', JSON.stringify(meta), true), 'Cannot save metadata')
  local key = p.frames_dir .. '/' .. d.drone_id
  if previous[key] then os.remove(previous[key]) end
  previous[key] = final
  saved, captureMs = saved + 1, meta.capture_ms
  lastError = ''
end

local function renderOne(p, d)
  assert(type(d.drone_id) == 'number' and d.drone_id >= 0 and d.drone_id < 32 and d.drone_id % 1 == 0, 'Invalid drone ID')
  if busy[d.drone_id] then return end
  local pos, target = vector(d.pos), vector(d.look_at)
  -- Telemetry is buffered; aim at the car's current in-game position when possible.
  if type(d.car_id) == 'number' then
    local car = ac.getCar(d.car_id)
    if car then target = car.position + vec3(0, 0.6, 0) end
  end
  local direction = target - pos
  if direction:length() < 0.01 then return end
  direction:normalize()
  local up = math.abs(direction.y) > 0.99 and vec3(0, 0, 1) or vec3(0, 1, 0)
  local fov = math.max(15, math.min(120, tonumber(d.fov) or 55))
  io.createDir(p.frames_dir)
  sequence = sequence + 1
  local name = string.format('drone_%d_%s_%d.jpg', d.drone_id, p.run_id, sequence)
  local temp = p.frames_dir .. '/' .. name .. '.tmp.jpg'
  local started = os.preciseClock()
  if p.method == 'main_camera' then
    if not mainAllowed then return end
    -- script.update keeps the camera aimed; allow it to render before capture.
    if not camera or mainId ~= d.drone_id or cameraAge < 0.1 then return end
    busy[d.drone_id] = true
    ac.makeScreenshot(temp, ac.ScreenshotFormat.JPG, function(err)
      busy[d.drone_id] = nil
      if err then lastError = tostring(err); return end
      local ok, e = pcall(publish, p, d, temp, name, started)
      if not ok then lastError = tostring(e) end
    end)
  else
    local w, h = math.max(160, math.min(1280, p.width or 640)), math.max(90, math.min(720, p.height or 360))
    local entry = shots[d.drone_id]
    if not entry or entry.w ~= w or entry.h ~= h then
      if entry then entry.shot:dispose() end
      local shot = ac.GeometryShot(ac.findNodes('sceneRoot:yes'), vec2(w, h), 1, true, render.AntialiasingMode.YEBIS)
      shot:setSky(true):setClippingPlanes(0.1, 8000):setOriginalLighting(true)
      shot:setShadersType(render.ShadersType.Main)
      shot:setTransparentPass(true):setParticles(true):setMaxLayer(5)
      entry = {shot = shot, w = w, h = h}; shots[d.drone_id] = entry
    end
    entry.shot:update(pos, direction, up, fov)
    entry.shot:save(temp, ac.ImageFormat.JPG)
    publish(p, d, temp, name, started)
  end
end

function script.update(dt)
  poll, statsClock = poll + dt, statsClock + dt
  if poll >= 0.1 then
    poll = 0
    local cautionOK, cautionData = pcall(function() return JSON.parse(io.load(cautionInbox) or '{}') end)
    cautionPacket = cautionOK and type(cautionData) == 'table' and cautionData or nil
    local ok, parsed = pcall(function() return JSON.parse(io.load(inbox) or '{}') end)
    if ok and type(parsed) == 'table' and (not packet or packet.run_id ~= parsed.run_id) then nextCapture = {} end
    packet = ok and type(parsed) == 'table' and parsed or nil
  end
  -- Keep baseline FPS observable even with capture paused.
  if statsClock >= 1 and packet and packet.frames_dir then
    statsClock = 0
    pcall(function()
      io.save(packet.frames_dir .. '/status.json', JSON.stringify({run_id = packet.run_id,
        game_fps = ac.getSim().fps, saved_frames = saved, last_capture_ms = captureMs,
        paused = paused, poses_fresh = fresh(),
        method = packet.method, error = lastError, time = os.time()}), true)
    end)
  end
  if paused or not fresh() then releaseCamera(); return end
  local p = packet
  local drones = p.drones
  if p.method == 'main_camera' then
    local selected = nil
    for _, d in ipairs(drones) do if d.drone_id == p.selected_drone then selected = d end end
    if not selected or not mainAllowed then releaseCamera(); return end
    local ok, err = pcall(function()
      if not camera then
        local why
        camera, why = ac.grabCamera('Marshal Fly: selected drone feed')
        assert(camera, why or 'Camera unavailable')
      end
      if mainId ~= selected.drone_id then mainId, cameraAge = selected.drone_id, 0 end
      local pos, target = vector(selected.pos), vector(selected.look_at)
      if type(selected.car_id) == 'number' then
        local car = ac.getCar(selected.car_id)
        if car then target = car.position + vec3(0, 0.6, 0) end
      end
      camera.transform.position:set(pos)
      camera.transform.look:set((target - pos):normalize())
      camera.fov = selected.fov or 55
      cameraAge = cameraAge + dt
    end)
    if not ok then lastError = tostring(err); releaseCamera(); return end
    drones = {selected}
  else releaseCamera() end
  -- One readback per game frame, with slower patrol feeds and no catch-up bursts.
  local now = os.preciseClock()
  for _ = 1, #drones do
    cursor = cursor % #drones + 1
    local d = drones[cursor]
    local key = p.run_id .. ':' .. d.drone_id
    local incident = type(d.event_id) == 'string' and d.event_id ~= ''
    local fps = math.max(1, math.min(10, (incident or p.method == 'main_camera') and (p.fps or 4) or (p.patrol_fps or 2)))
    if now >= (nextCapture[key] or 0) then
      nextCapture[key] = now + 1 / fps
      local ok, err = pcall(renderOne, p, d)
      if not ok then lastError = tostring(err) end
      break
    end
  end
end

function script.windowMain(dt)
  ui.text('Marshal Drone Cams')
  if ui.checkbox('Show Marshal yellow flags (display only)', showCautions) then showCautions = not showCautions end
  ui.text(fresh() and ('LIVE / ' .. packet.method) or 'Waiting for live brain poses')
  if ui.checkbox('Pause capture (baseline FPS measurement)', paused) then paused = not paused end
  if ui.checkbox('Allow main-camera takeover (spectating only)', mainAllowed) then
    mainAllowed = not mainAllowed
    if not mainAllowed then releaseCamera() end
  end
  ui.text(string.format('Game: %.1f FPS | Saved: %d', ac.getSim().fps, saved))
  ui.text(string.format('Last capture: %.1f ms', captureMs))
  ui.textWrapped('GeometryShot can omit distant cars. Check the incident car visually. Select main_camera in settings and allow takeover here if needed.')
  if lastError ~= '' then ui.textWrapped('Error: ' .. lastError) end
end

ac.onRelease(function()
  releaseCamera()
  for _, entry in pairs(shots) do entry.shot:dispose() end
end)
