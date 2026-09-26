// Race-control dashboard: listens to telemetry (8765) and brain (8766),
// draws the 2D map, and hands the same state to the 3D view when toggled.

const TEL_URL = "ws://localhost:8765";
const BRAIN_URL = "ws://localhost:8766";

export const state = {
  track: null,          // {centerline: [[tp,x,y,z,v]...], length_m}
  t: 0,
  cars: new Map(),      // car_id -> frame
  prevCars: new Map(),
  drones: [],           // DroneState[]
  events: new Map(),    // id -> RiskEvent
  report: null,
};

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

connect(TEL_URL, "conn-tel", (m) => {
  if (m.type === "track") { state.track = m; fitMap(); view3d?.setTrack(m); }
  else if (m.type === "frames") {
    state.t = m.t;
    state.prevCars = state.cars;
    state.cars = new Map(m.cars.map((c) => [c.car_id, c]));
  }
});

connect(BRAIN_URL, "conn-brain", (m) => {
  if (m.type === "drones") {
    state.drones = m.drones; renderSide();
    if (m.events) {
      const ids = m.events.map((e) => e.id + e.kind).join();
      if (ids !== state.eventKey) { state.eventKey = ids; state.events = new Map(m.events.map((e) => [e.id, e])); renderAlerts(); }
    }
  }
  else if (m.type === "risk") { state.events.set(m.event.id, m.event); renderAlerts(); }
  else if (m.type === "risk_end") { state.events.delete(m.id); renderAlerts(); }
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
  const cl = state.track.centerline;
  const xs = cl.map((p) => p[1]), zs = cl.map((p) => p[3]);
  const [x0, x1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  const pad = 40;
  const s = Math.min((r.width - 2 * pad) / (x1 - x0), (r.height - 2 * pad) / (z1 - z0));
  xf = { s, ox: pad + ((r.width - 2 * pad) - (x1 - x0) * s) / 2 - x0 * s, oy: pad + ((r.height - 2 * pad) - (z1 - z0) * s) / 2 - z0 * s };
}
const P = (x, z) => [x * xf.s + xf.ox, z * xf.s + xf.oy];
window.addEventListener("resize", () => { fitMap(); view3d?.resize(); });

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function draw2d(now) {
  const r = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, r.width, r.height);
  if (!state.track) {
    ctx.fillStyle = css("--muted"); ctx.font = "15px system-ui";
    ctx.fillText("Waiting for telemetry on " + TEL_URL + " …", 24, 40);
    return;
  }
  // track
  const cl = state.track.centerline;
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.beginPath();
  cl.forEach((p, i) => { const [a, b] = P(p[1], p[3]); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); });
  ctx.closePath();
  ctx.strokeStyle = css("--track"); ctx.lineWidth = Math.max(6, 12 * xf.s); ctx.stroke();
  const [sx, sy] = P(cl[0][1], cl[0][3]);
  ctx.fillStyle = "#fff"; ctx.fillRect(sx - 2, sy - 8, 4, 16);

  // events
  const pulse = (Math.sin(now / 220) + 1) / 2;
  for (const e of state.events.values()) {
    const [a, b] = P(e.x, e.z);
    const col = e.type === "incident" ? css("--inc") : css("--pred");
    ctx.strokeStyle = col; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(a, b, 14 + pulse * 10, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = col; ctx.font = "600 12px system-ui";
    ctx.fillText(`${e.type === "incident" ? "INCIDENT" : "RISK"} · ${e.kind}${e.eta_s ? " · " + e.eta_s + "s" : ""}`, a + 18, b - 16);
  }

  // cars
  ctx.font = "600 10px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const c of state.cars.values()) {
    const [a, b] = P(c.x, c.z);
    const flagged = [...state.events.values()].some((e) => e.car_ids.includes(c.car_id));
    ctx.fillStyle = flagged ? css("--inc") : css("--car");
    ctx.beginPath(); ctx.arc(a, b, 7, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#0d1214"; ctx.fillText(c.car_id, a, b + 0.5);
  }

  // drones
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

// ---------- side panels ----------
function renderAlerts() {
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

function renderReport() {
  const r = state.report, el = document.getElementById("report");
  if (!r) return;
  el.classList.remove("empty");
  const chip = (ok, label) => `<span class="chip ${ok ? "bad" : ""}">${label}</span>`;
  el.innerHTML = `<div class="sum">${r.summary}</div><div class="chips">` +
    chip(r.stopped, r.stopped ? "stationary" : "moving") +
    chip(r.on_racing_line, r.on_racing_line ? "on racing line" : "off line") +
    chip(r.debris, r.debris ? "debris" : "no debris") +
    chip(r.smoke, r.smoke ? "smoke" : "no smoke") +
    (r.cars_approaching_s != null ? chip(r.cars_approaching_s < 5, `next car ${r.cars_approaching_s}s`) : "") +
    `</div>` + (r.frame_path ? `<img src="${r.frame_path}" alt="drone view" style="margin-top:8px;border-radius:6px">` : "");
}

function renderSide() {
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
    document.getElementById("spikes").textContent = a.spikes.toLocaleString();
  }
}

// ---------- 2D / 3D toggle ----------
let view3d = null;
const b2 = document.getElementById("btn-2d"), b3 = document.getElementById("btn-3d");
b2.onclick = () => { document.body.classList.remove("mode3d"); b2.classList.add("on"); b3.classList.remove("on"); canvas.hidden = false; document.getElementById("view3d").hidden = true; };
b3.onclick = async () => {
  document.body.classList.add("mode3d"); b3.classList.add("on"); b2.classList.remove("on");
  canvas.hidden = true; document.getElementById("view3d").hidden = false;
  if (!view3d) {
    const { View3D } = await import("./view3d.js");
    view3d = new View3D(document.getElementById("view3d"), state);
    if (state.track) view3d.setTrack(state.track);
  }
  view3d.resize();
};
for (const [id, mode] of [["cam-orbit", "orbit"], ["cam-fly", "fly"], ["cam-incident", "incident"]]) {
  document.getElementById(id).onclick = () => view3d?.setCamera(mode);
}

function loop(now) {
  document.getElementById("clock").textContent = `t = ${state.t.toFixed(1)} s`;
  if (!canvas.hidden) draw2d(now); else view3d?.update(now);
  requestAnimationFrame(loop);
}
fitMap();
requestAnimationFrame(loop);
