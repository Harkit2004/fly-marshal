// Race-control dashboard: listens to telemetry (8765) and brain (8766), draws the 2D map,
// and hands the same state to the 3D view (view3d.js) and the fly-brain viewer (flybrain3d.js).

const TEL_URL = "ws://localhost:8765";
const BRAIN_URL = "ws://localhost:8766";
const VER = new URL(import.meta.url).search;   // "?v=N" from index.html, passed on so modules refresh together

export const state = {
  track: null,          // {track_id, centerline: [[tp,x,y,z,v]...], widths?, length_m}
  t: 0,
  cars: new Map(), prevCars: new Map(), frameAt: 0, frameDt: 66,       // interpolation between ticks
  drones: [], prevDrones: new Map(), dronesAt: 0, dronesDt: 66,
  events: new Map(),
  report: null,
};
const ui = { labels: true, feeds: true, tiles: document.getElementById("tiles") };
const gameFrames = new Map();
const escapeHTML = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));

function* carsPlayerLast() {
  const id = state.track?.settings?.scene?.player_car_id ?? 0;
  for (const car of state.cars.values()) if (car.car_id !== id) yield car;
  if (state.cars.has(id)) yield state.cars.get(id);
}

function updateGameTiles() {
  const now = performance.now();
  for (const tile of ui.tiles.querySelectorAll('[data-drone]')) {
    const frame = gameFrames.get(+tile.dataset.drone);
    const fresh = frame && now < frame.expires;
    const img = tile.querySelector('.game-frame');
    const badge = tile.querySelector('.source');
    if (!img || !badge) continue;
    if (fresh && img.getAttribute('src') !== frame.image) img.src = frame.image;
    img.hidden = !fresh;
    tile.dataset.source = fresh ? 'game' : '3d';
    badge.textContent = fresh ? 'GAME' : '3D';
  }
}
setInterval(updateGameTiles, 100); // expire even when telemetry or brain stops
let view3d = null, flyBrain = null;
let sideDirty = false, alertsDirty = false, reportDirty = false;

// ---------- websockets ----------
function connect(url, dotId, onMsg) {
  const dot = document.getElementById(dotId);
  const open = () => {
    const ws = new WebSocket(url);
    ws.onopen = () => dot.classList.add("up");
    ws.onclose = () => { dot.classList.remove("up"); setTimeout(open, 1500); };
    ws.onmessage = (e) => onMsg(JSON.parse(e.data));
  };
  open();
}

const ema = (old, v) => (old ? old * 0.8 + v * 0.2 : v);

function resetSession(sessionId) {
  state.sessionId = sessionId;
  state.track = null; state.t = 0;
  state.cars.clear(); state.prevCars.clear();
  state.drones = []; state.prevDrones.clear();
  state.events.clear(); state.eventKey = ''; state.report = null;
  state.frameAt = 0; state.dronesAt = 0;
  gameFrames.clear();
  ui.tiles.replaceChildren();
  document.querySelectorAll('[data-drone]').forEach(e => e.remove());
  document.getElementById('report').textContent = 'Waiting for the new session…';
  view3d?.resetSession();
  flyBrain?.fire([]);
  document.getElementById('spikes').textContent = '0';
  document.getElementById('fly-kind').textContent = 'Waiting for the new session…';
  for (const k of ['forward', 'turn', 'climb']) document.getElementById('b-' + k).style.width = '0%';
  renderAlerts(); renderSide();
}

connect(TEL_URL, "conn-tel", (m) => {
  if (m.type === 'session_reset') resetSession(m.session_id);
  else if (m.type === "track") { resetSession(m.session_id); state.track = m; fitMap(); view3d?.setTrack(m); }
  else if (m.type === "frames") {
    const now = performance.now();
    state.frameDt = Math.min(250, ema(state.frameDt, now - state.frameAt));
    state.frameAt = now;
    state.t = m.t;
    state.prevCars = state.cars;
    state.cars = new Map(m.cars.map((c) => [c.car_id, c]));
  }
});

connect(BRAIN_URL, "conn-brain", (m) => {
  if (m.session_id != null && m.session_id !== state.sessionId) return;
  if (m.type === 'game_frames_reset') {
    gameFrames.clear(); state.report = null;
    document.getElementById('report').textContent = 'Waiting for incident report';
    updateGameTiles();
  }
  else if (m.type === 'game_frames') {
    for (const f of m.frames || []) {
      if (f.source !== 'game' || !Number.isInteger(f.drone_id) || !f.image?.startsWith('data:image/jpeg;base64,')) continue;
      gameFrames.set(f.drone_id, {image: f.image, expires: performance.now() + Math.max(0, 2 - f.age_s) * 1000});
    }
    updateGameTiles();
  }
  if (m.type === "drones") {
    const now = performance.now();
    state.dronesDt = Math.min(250, ema(state.dronesDt, now - state.dronesAt));
    state.dronesAt = now;
    state.prevDrones = new Map(state.drones.map((d) => [d.drone_id, d]));
    state.drones = m.drones;
    renderSide();
    syncTiles();
    const fly = m.drones.find((d) => d.pilot === "fly");
    if (fly?.fly_activity?.source === "flywire") flyBrain?.fire(fly.fly_activity.fired);
    if (m.events) {
      const ids = m.events.map((e) => e.id + e.kind).join();
      state.events = new Map(m.events.map((e) => [e.id, e]));
      if (ids !== state.eventKey) { state.eventKey = ids; renderAlerts(); }
      if (state.report && !state.events.has(state.report.event_id)) { state.report = null; renderReport(); }
    }
  }
  else if (m.type === "risk") { state.events.set(m.event.id, m.event); renderAlerts(); }
  else if (m.type === "risk_end") {
    state.events.delete(m.id); renderAlerts();
    if (state.report?.event_id === m.id) { state.report = null; renderReport(); }
  }
  else if (m.type === "report") { state.report = m.report; renderReport(); }
});

// ---------- 2D map ----------
const canvas = document.getElementById("map2d");
const ctx = canvas.getContext("2d");
let xf = { s: 1, ox: 0, oy: 0 };

function fitMap() {
  const dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = r.width * dpr; canvas.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!state.track) return;
  xf = fitTransform(state.track.centerline, r.width, r.height, 40);
}
function fitTransform(cl, w, h, pad) {
  const xs = cl.map((p) => p[1]), zs = cl.map((p) => p[3]);
  const [x0, x1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  const s = Math.min((w - 2 * pad) / (x1 - x0), (h - 2 * pad) / (z1 - z0));
  return { s, ox: pad + ((w - 2 * pad) - (x1 - x0) * s) / 2 - x0 * s, oy: pad + ((h - 2 * pad) - (z1 - z0) * s) / 2 - z0 * s };
}
const P = (x, z, t = xf) => [x * t.s + t.ox, z * t.s + t.oy];
window.addEventListener("resize", () => { fitMap(); view3d?.resize(); flyBrain?.resize(); });

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function draw2d(now) {
  const r = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, r.width, r.height);
  if (!state.track) {
    ctx.fillStyle = css("--muted"); ctx.font = "15px system-ui";
    ctx.fillText("Waiting for telemetry on " + TEL_URL + " …", 24, 40);
    return;
  }
  const cl = state.track.centerline;
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.beginPath();
  cl.forEach((p, i) => { const [a, b] = P(p[1], p[3]); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); });
  ctx.closePath();
  ctx.strokeStyle = css("--track"); ctx.lineWidth = Math.max(6, 12 * xf.s); ctx.stroke();
  const [sx, sy] = P(cl[0][1], cl[0][3]);
  ctx.fillStyle = "#fff"; ctx.fillRect(sx - 2, sy - 8, 4, 16);

  const pulse = (Math.sin(now / 220) + 1) / 2;
  let k = 0;
  for (const e of state.events.values()) {
    const [a, b] = P(e.x, e.z);
    const col = e.type === "incident" ? css("--inc") : css("--pred");
    ctx.strokeStyle = col; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(a, b, 14 + pulse * 10, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = col; ctx.font = "600 12px system-ui"; ctx.textAlign = "left";
    ctx.fillText(`${e.type === "incident" ? "INCIDENT" : "RISK"} · #${e.car_ids[0]} ${e.kind}${e.eta_s ? " · " + e.eta_s + "s" : ""}`, a + 18, b - 16 - 14 * (k++ % 3));
  }

  ctx.font = "600 10px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  const flagged = new Set([...state.events.values()].flatMap((e) => e.car_ids));
  for (const c of carsPlayerLast()) {
    const [a, b] = P(c.x, c.z);
    const player = c.car_id === (state.track?.settings?.scene?.player_car_id ?? 0);
    ctx.fillStyle = player ? '#32e875' : flagged.has(c.car_id) ? css("--inc") : css("--car");
    ctx.beginPath(); ctx.arc(a, b, player ? 9 : 7, 0, Math.PI * 2); ctx.fill();
    if (player) { ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2; ctx.stroke(); }
    ctx.fillStyle = "#0d1214"; ctx.fillText(c.car_id, a, b + 0.5);
    if (player) { ctx.fillStyle = '#32e875'; ctx.fillText('YOU', a, b - 16); }
  }
  for (const d of state.drones) {
    const [a, b] = P(d.x, d.z);
    const col = d.pilot === "fly" ? css("--fly") : css("--pid");
    if (d.target && d.mode !== "patrol") {
      const [ta, tb] = P(d.target[0], d.target[2]);
      ctx.setLineDash([4, 5]); ctx.strokeStyle = col; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(ta, tb); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.moveTo(a, b - 9); ctx.lineTo(a + 8, b); ctx.lineTo(a, b + 9); ctx.lineTo(a - 8, b); ctx.closePath(); ctx.fill();
    ctx.textAlign = "left"; ctx.font = "600 11px system-ui";
    ctx.fillText(`${d.pilot === "fly" ? "🪰 " : ""}D${d.drone_id} ${d.mode}`, a + 11, b + 12);
    ctx.textAlign = "center"; ctx.font = "600 10px system-ui";
  }
}

// ---------- 3D overlays: minimap, drone tiles, HUD ----------
const mini = document.getElementById("minimap"), mctx = mini.getContext("2d");
function drawMinimap() {
  if (!state.track) return;
  const dpr = window.devicePixelRatio || 1, r = mini.getBoundingClientRect();
  if (mini.width !== Math.round(r.width * dpr)) { mini.width = r.width * dpr; mini.height = r.height * dpr; }
  mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  mctx.clearRect(0, 0, r.width, r.height);
  const t = fitTransform(state.track.centerline, r.width, r.height, 12);
  mctx.beginPath();
  state.track.centerline.forEach((p, i) => { const [a, b] = P(p[1], p[3], t); i ? mctx.lineTo(a, b) : mctx.moveTo(a, b); });
  mctx.closePath(); mctx.strokeStyle = "#56625f"; mctx.lineWidth = 3; mctx.stroke();
  const flagged = new Set([...state.events.values()].flatMap((e) => e.car_ids));
  for (const c of carsPlayerLast()) {
    const [a, b] = P(c.x, c.z, t);
    const player = c.car_id === (state.track?.settings?.scene?.player_car_id ?? 0);
    mctx.fillStyle = player ? '#32e875' : flagged.has(c.car_id) ? css("--inc") : "#d8e2df";
    const radius = player ? 3 : 1.5;
    mctx.fillRect(a - radius, b - radius, radius * 2, radius * 2);
  }
  for (const d of state.drones) {
    const [a, b] = P(d.x, d.z, t);
    mctx.fillStyle = d.pilot === "fly" ? css("--fly") : css("--pid");
    mctx.beginPath(); mctx.arc(a, b, d.drone_id === view3d?.selected ? 5 : 3.5, 0, Math.PI * 2); mctx.fill();
  }
  if (view3d) {           // main camera position + heading
    const cam = view3d.camera, dir = cam.getWorldDirection(cam.position.clone());
    const [a, b] = P(cam.position.x, cam.position.z, t);
    mctx.strokeStyle = "#fff"; mctx.lineWidth = 1.5;
    mctx.beginPath(); mctx.moveTo(a, b); mctx.lineTo(a + dir.x * 14, b + dir.z * 14); mctx.stroke();
    mctx.beginPath(); mctx.arc(a, b, 3, 0, Math.PI * 2); mctx.stroke();
  }
}

function syncTiles() {
  const sel = document.getElementById("drone-select");
  for (const d of state.drones) {
    if (!ui.tiles.querySelector(`[data-drone="${d.drone_id}"]`)) {
      const tile = document.createElement("div");
      tile.className = "tile"; tile.dataset.drone = d.drone_id;
      tile.innerHTML = `<div class="feed"><img class="game-frame" alt="AC game camera" hidden></div><div class="tag"><span class="source">3D</span><span class="name"></span><span class="mode"></span></div>`;
      tile.onclick = () => { selectDrone(d.drone_id); setCam("drone"); };
      ui.tiles.appendChild(tile);
      const b = document.createElement("button");
      b.textContent = `${d.pilot === "fly" ? "🪰" : "D"}${d.drone_id}`; b.dataset.drone = d.drone_id;
      b.title = `Select drone ${d.drone_id} (key ${d.drone_id + 1})`;
      b.onclick = () => selectDrone(d.drone_id);
      sel.appendChild(b);
    }
    const tile = ui.tiles.querySelector(`[data-drone="${d.drone_id}"]`);
    tile.querySelector(".name").textContent = `D${d.drone_id} · ${d.pilot === "fly" ? "FLY" : "PID"}`;
    tile.querySelector(".mode").textContent = d.mode.toUpperCase();
  }
  const selId = view3d?.selected ?? 0;
  ui.tiles.querySelectorAll(".tile").forEach((t) => t.classList.toggle("sel", +t.dataset.drone === selId));
  sel.querySelectorAll("button").forEach((b) => b.classList.toggle("on", +b.dataset.drone === selId));
}

function updateHud() {
  const hud = document.getElementById("hud");
  hud.hidden = !(view3d && view3d.camMode === "drone" && !document.getElementById("view3d").hidden);
  if (hud.hidden) return;
  const info = view3d.hudInfo();
  if (!info) return;
  const { d, alt, speed, fov } = info;
  const ev = d.event_id && state.events.get(d.event_id);
  document.getElementById("hud-title").textContent = `DRONE ${d.drone_id} · ${d.pilot === "fly" ? "FLY BRAIN" : "PID"} · ${d.mode.toUpperCase()}${ev ? ` · TRACKING #${ev.car_ids[0]} ${ev.kind.toUpperCase()}` : ""}`;
  document.getElementById("hud-time").textContent = `T+${state.t.toFixed(1)}s`;
  document.getElementById("hud-stats").innerHTML =
    `<span>ALT ${alt.toFixed(0)} m</span><span>SPD ${speed.toFixed(0)} km/h</span><span>ZOOM ${(55 / fov).toFixed(1)}×</span>` +
    `<span>X ${d.x.toFixed(0)} Z ${d.z.toFixed(0)}</span>`;
}

// ---------- side panels ----------
function renderAlerts() { alertsDirty = true; }
function paintAlerts() {
  const ul = document.getElementById("alerts");
  const evs = [...state.events.values()].sort((a, b) => (a.type === "incident" ? -1 : 1) - (b.type === "incident" ? -1 : 1) || b.t - a.t);
  ul.innerHTML = evs.length ? "" : '<li class="empty">No active events</li>';
  for (const e of evs) {
    const li = document.createElement("li");
    li.className = e.type;
    li.innerHTML = `<b>${e.type === "incident" ? "Incident" : "Predicted"}</b> · car #${e.car_ids.join(", #")} · ${e.kind}` +
      `<br><span class="muted">t=${e.t.toFixed(1)} s · severity ${e.severity}${e.eta_s ? " · in " + e.eta_s + " s" : ""}</span>`;
    ul.appendChild(li);
  }
}

function renderReport() { reportDirty = true; }
function paintReport() {
  const r = state.report, el = document.getElementById("report");
  if (!r) return;
  el.classList.remove("empty");
  const chip = (bad, label) => `<span class="chip ${bad ? "bad" : ""}">${label}</span>`;
  // debris / smoke come only from the vision report; null means nobody has looked yet
  const seen = (v, what) => v == null ? `<span class="chip unknown" title="needs the drone camera + vision model">${what}: ?</span>` : chip(v, v ? what : `no ${what}`);
  el.innerHTML = `<div class="sum">${escapeHTML(r.summary)}</div><div class="chips">` +
    (r.stopped == null ? seen(null, "stopped") : chip(r.stopped, r.stopped ? "stationary" : "moving")) +
    (r.on_racing_line == null ? seen(null, "racing line") : chip(r.on_racing_line, r.on_racing_line ? "on racing line" : "off line")) +
    seen(r.debris, "debris") + seen(r.smoke, "smoke") +
    seen(r.driver_out, "driver out") + seen(r.blocking, "blocking") +
    (r.cars_approaching_s != null ? chip(r.cars_approaching_s < 5, `next car ${r.cars_approaching_s}s`) : "") +
    `</div>` + (r.source === 'game_vision' && r.frame_path?.startsWith('data:image/jpeg;base64,') ? `<img src="${escapeHTML(r.frame_path)}" alt="Game frame assessed for this report" style="margin-top:8px;border-radius:6px;max-width:100%">` : "");
}

function renderSide() { sideDirty = true; }
function paintSide() {
  const ul = document.getElementById("drones");
  ul.innerHTML = "";
  for (const d of state.drones) {
    const sp = Math.hypot(d.vx, d.vy, d.vz) * 3.6;
    const li = document.createElement("li");
    li.innerHTML = `<span class="pilot-${d.pilot}">D${d.drone_id} · ${d.pilot === "fly" ? "fly brain" : "PID"}</span>` +
      `<span><span class="mode ${d.mode}">${d.mode}</span> ${sp.toFixed(0)} km/h</span>`;
    ul.appendChild(li);
  }
  const fly = state.drones.find((d) => d.pilot === "fly");
  const a = fly?.fly_activity;
  if (a && a.forward !== undefined) {
    document.getElementById("b-forward").style.width = `${a.forward * 100}%`;
    for (const k of ["turn", "climb"]) {
      const v = Math.max(-1, Math.min(1, a[k])), el = document.getElementById("b-" + k);
      el.style.left = `${50 + Math.min(0, v) * 50}%`; el.style.width = `${Math.abs(v) * 50}%`;
    }
    const real = a.source === "flywire";
    document.getElementById("spikes").textContent = real ? a.spikes.toLocaleString() : "0";
    if (real && a.groups) for (const [g, v] of Object.entries(a.groups)) {
      const b = document.getElementById("rg-" + g);
      if (b) b.textContent = `${(v * 100).toFixed(1)}%`;
    }
    if (flyBrain?.brain) document.getElementById("fly-kind").textContent = real
      ? [`FlyWire v783${a.synapse_stride > 1 ? ` (1/${a.synapse_stride} of synapses)` : ""}`,
         a.model === "corrected" ? "corrected LIF (τm 20 ms)" : "upstream RealFlyBrain",
         "heading from simulated photoreceptor L/R firing",
         a.assisted?.length ? `${a.assisted.join(" + ")} assisted` : "",
         a.brain_hz ? `brain steps at ${a.brain_hz} Hz` : ""].filter(Boolean).join(" · ")
      : "connectome not running (placeholder controller): no neural activity shown";
  }
}

// ---------- fly brain viewer ----------
import("./flybrain3d.js" + VER).then(({ FlyBrain3D, GROUP_INFO }) => {
  const legend = document.getElementById("fly-legend");
  const kind = document.getElementById("fly-kind");
  flyBrain = new FlyBrain3D(document.getElementById("flybrain"), (status) => {
    if (status === "missing") {
      kind.textContent = "brain data not built: run python tools/fetch_flywire.py";
      return;
    }
    const n = flyBrain.brain.root_id.length;
    legend.innerHTML = flyBrain.groups.map((g, k) => flyBrain.groupCount[k] ? `<li><i style="background:#${(GROUP_INFO[g] || GROUP_INFO.other).color.toString(16).padStart(6, "0")}"></i>${(GROUP_INFO[g] || GROUP_INFO.other).label}<b id="rg-${g}">${flyBrain.groupCount[k]}</b></li>` : "").join("");
    legend.title = `${n.toLocaleString()} real FlyWire neurons at their real positions; counts until activity arrives, then % firing`;
  });
});

// ---------- 2D / 3D, cameras, selection ----------
const b2 = document.getElementById("btn-2d"), b3 = document.getElementById("btn-3d");
const stage3d = [document.getElementById("view3d"), mini, ui.tiles];
b2.onclick = () => {
  document.body.classList.remove("mode3d"); b2.classList.add("on"); b3.classList.remove("on");
  canvas.hidden = false; stage3d.forEach((e) => (e.hidden = true));
};
b3.onclick = async () => {
  document.body.classList.add("mode3d"); b3.classList.add("on"); b2.classList.remove("on");
  canvas.hidden = true; stage3d.forEach((e) => (e.hidden = false));
  ui.tiles.hidden = !ui.feeds;
  if (!view3d) {
    const { View3D } = await import("./view3d.js" + VER);
    view3d = new View3D(document.getElementById("view3d"), state, ui);
    if (state.track) view3d.setTrack(state.track);
  }
  view3d.resize();
};
function setCam(mode) {
  view3d?.setCamera(mode);
  document.querySelectorAll("[data-cam]").forEach((b) => b.classList.toggle("on", b.dataset.cam === mode));
}
function selectDrone(id) { view3d?.selectDrone(id); syncTiles(); }
document.querySelectorAll("[data-cam]").forEach((b) => (b.onclick = () => setCam(b.dataset.cam)));
document.getElementById("tg-labels").onchange = (e) => (ui.labels = e.target.checked);
document.getElementById("tg-feeds").onchange = (e) => { ui.feeds = e.target.checked; ui.tiles.hidden = !ui.feeds || canvas.hidden === false; };
const CAMS = ["orbit", "chase", "drone", "tv", "incident"];
window.addEventListener("keydown", (e) => {
  if (!view3d || canvas.hidden === false) return;
  if (e.key >= "1" && e.key <= "9") selectDrone(+e.key - 1);
  if (e.key === "c") setCam(CAMS[(CAMS.indexOf(view3d.camMode) + 1) % CAMS.length]);
});

let lastPaint = 0, lastPanelPaint = 0;
function loop(now) {
  requestAnimationFrame(loop);
  const fps = Math.max(10, Math.min(60, state.track?.settings?.scene?.dashboard_fps || 30));
  if (document.hidden || now - lastPaint < 1000 / fps) return;
  const elapsed = Math.min(0.2, (now - lastPaint) / 1000);
  lastPaint = now - ((now - lastPaint) % (1000 / fps));
  if (now - lastPanelPaint >= 100) {
    if (sideDirty) { paintSide(); sideDirty = false; }
    if (alertsDirty) { paintAlerts(); alertsDirty = false; }
    if (reportDirty) { paintReport(); reportDirty = false; }
    lastPanelPaint = now;
  }
  document.getElementById("clock").textContent = `t = ${state.t.toFixed(1)} s`;
  if (!canvas.hidden) draw2d(now);
  else if (view3d) { view3d.update(); drawMinimap(); updateHud(); }
  const brainBox = document.getElementById('flybrain').getBoundingClientRect();
  if (brainBox.bottom > 0 && brainBox.top < innerHeight) flyBrain?.update(elapsed);
}
fitMap();
requestAnimationFrame(loop);
