-- Advisory local-race AI control. No teleporting, forced steering or native flag override.
local M = {enabled = true, status = 'Waiting for caution feed', owned = {}, session = nil}
local function release(id, state)
  pcall(physics.setAITopSpeed, id, 1e9) -- documented removal value
  pcall(physics.setAIAggression, id, state.aggression)
end
function M.reset()
  for id, state in pairs(M.owned) do release(id, state) end
  M.owned = {}
end
local function zoneAt(pos, ranges)
  for _, r in ipairs(ranges) do if pos >= r[1] and pos <= r[2] then return true end end
  return false
end
function M.update(packet, dt)
  local sim = ac.getSim()
  local valid = M.enabled and packet and packet.ai_enabled == true and packet.source == 'live'
    and type(packet.sent_at) == 'number' and math.abs(os.time()-packet.sent_at) < 2
    and packet.track_id == ac.getTrackFullID('/') and type(packet.ranges) == 'table'
    and not sim.isOnlineRace and not sim.isReplayActive and not sim.isReplayOnlyMode
  if not valid then M.reset(); M.status = 'AI yellows off / no fresh live zones'; return end
  if not physics or not physics.allowed or not physics.allowed() then
    M.reset(); M.status = 'AI yellows unavailable: CSP physics access is disabled'; return
  end
  if M.session ~= packet.session_id then M.reset(); M.session = packet.session_id end
  local length = math.max(1, sim.trackLengthM or 1)
  local cap = math.max(20, math.min(150, tonumber(packet.speed_kmh) or 80))
  local approach = math.max(0, math.min(1500, tonumber(packet.approach_m) or 250))
  local gapTarget = math.max(10, tonumber(packet.gap_m) or 20)
  local exempt = {}
  for _, id in ipairs(packet.incident_cars or {}) do exempt[id] = true end
  local cars, desired = {}, {}
  for id=0,sim.carsCount-1 do
    local c = ac.getCar(id)
    if c and c.isConnected ~= false and not c.isInPit and not c.isInPitlane then cars[id] = c end
  end
  for id,c in pairs(cars) do
    if id ~= 0 and c.isAIControlled and not exempt[id] then
      local pos = c.splinePosition % 1
      local inside = zoneAt(pos,packet.ranges)
      local distance = math.huge
      if not inside then
        for _,r in ipairs(packet.ranges) do distance = math.min(distance, ((r[1]-pos)%1)*length) end
      end
      if inside or distance < approach then
        -- Speed envelope: 3 m/s^2 nominal braking before the zone entrance.
        local target = inside and cap or math.sqrt((cap/3.6)^2 + 6*distance)*3.6
        local owned = M.owned[id]
        if not owned then
          owned = {aggression=c.aiAggression, limit=math.max(c.speedKmh,target)}
          M.owned[id] = owned
        end
        local leader = owned.leader and cars[owned.leader]
        if leader and (exempt[owned.leader] or leader.speedKmh < 5 or (leader.wheelsOutside or 0) >= 3) then leader=nil end
        if not leader then
          owned.leader=nil
          local best=math.huge
          for other,front in pairs(cars) do
            local gap=((front.splinePosition-pos)%1)*length
            if other~=id and not exempt[other] and front.speedKmh>=5 and (front.wheelsOutside or 0)<3
                and gap>0 and gap<math.min(150,best) then
              best=gap; owned.leader=other; leader=front
            end
          end
        end
        if leader then
          local gap=((leader.splinePosition-pos+.5)%1-.5)*length
          if gap > -30 and gap < 150 then
            target=math.min(target, math.max(0,leader.speedKmh + (gap-gapTarget)*1.2))
          else owned.leader=nil end
        end
        owned.limit=math.min(target,owned.limit+10*math.min(dt,.25))
        physics.setAITopSpeed(id,owned.limit)
        physics.setAIAggression(id,0)
        desired[id]=true
      end
    end
  end
  for id,owned in pairs(M.owned) do
    if not desired[id] then release(id,owned); M.owned[id]=nil end
  end
  local ids={}
  for id in pairs(M.owned) do ids[#ids+1]=id end
  M.status=string.format('AI yellow control: %d cars | %.0f km/h zone limit',#ids,cap)
  return ids
end
return M
