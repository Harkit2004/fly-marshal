-- Native flag override is local to this session, not a per-AI racing rule.
local M = {enabled=true, active=false, status='Native yellow: waiting for live zones'}
function M.reset()
  if M.active then
    local ok = pcall(physics.overrideRacingFlag, ac.FlagType.None)
    if ok then M.active=false end
  end
end
function M.update(p)
  local sim=ac.getSim()
  local inside=false
  if M.enabled and p and p.native_enabled ~= false and p.source=='live'
      and type(p.sent_at)=='number' and math.abs(os.time()-p.sent_at)<2
      and p.track_id==ac.getTrackFullID('/') and type(p.ranges)=='table'
      and not sim.isOnlineRace and not sim.isReplayActive and not sim.isReplayOnlyMode then
    local c=ac.getCar(0)
    if c and not c.isInPit and not c.isInPitlane then
      local pos=c.splinePosition%1
      for _,r in ipairs(p.ranges) do
        if type(r)=='table' and type(r[1])=='number' and type(r[2])=='number'
            and pos>=r[1] and pos<=r[2] then inside=true; break end
      end
    end
  end
  if not inside then M.reset(); M.status='Native yellow: inactive'; return end
  if not physics or not physics.allowed or not physics.allowed() or not physics.overrideRacingFlag then
    M.reset(); M.status='Native yellow unavailable: CSP physics access required'; return
  end
  local ok=pcall(physics.overrideRacingFlag,ac.FlagType.Caution)
  if ok then M.active=true; M.status='Native yellow: overriding AC flag in this zone'
  else M.reset(); M.status='Native yellow: override failed' end
end
return M
