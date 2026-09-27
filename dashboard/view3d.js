// 3D race-control view (Three.js).
//
//  - Track built from the centreline (+ half widths when the AI line has them): asphalt,
//    edge lines, kerbs and run-off in corners, sponsor boards, start gantry, grandstand, trees.
//  - Cars and drones are interpolated between telemetry ticks and animated (wheels, rotors,
//    drone pitch/roll, LEDs). Procedural models by default; glTF models from
//    settings.toml [models] / [tracks] replace them (see assets/models/README.md).
//  - Every drone carries a gimbal camera. Live feeds show as tiles; click one to fly it.
//    In drone cam: drag = pan/tilt the gimbal, wheel = zoom, double-click = back to auto-track.
//  - Camera modes: orbit, chase (orbit around the selected drone), drone cam, TV (nearest
//    trackside camera zooms on the action), incident.
//
// AC and Three.js are both y-up. If the track looks mirrored against the 2D map, set
// scene.mirror_z in settings.toml. All sizes below come from settings.toml [scene] / [models] /
// [tracks], delivered inside the "track" message; these are only the defaults.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { CarSkins } from './car-skins.js';
import { clone as cloneSkinned } from "three/addons/utils/SkeletonUtils.js";
import { cautionRanges } from './caution-zones.js?v=1';

const DEFAULT_HALF_WIDTH = 6;
const CFG = {
  player_car_id: 0,
  mirror_z: false,
  board_gap_m: 4,           // between track edge and sponsor boards
  board_height_m: 1.2,
  panel_length_m: 14,
  railing_depth_m: 0.25,
  railing_post_spacing_m: 4,
  railing_color: "#89959f",
  drone_scale: 3,           // drones are tiny at track scale; exaggerate for visibility
  car_scale: 1.4,
  kerb_radius_m: 220,       // tighter than this gets kerbs + run-off
  tv_camera_spacing_m: 380,
  terrain_detail: 220,
};
const SPONSOR_DIR = "assets/sponsors/";
const SPONSOR_VERSION = "17";
const MODEL_DIR = "assets/models/";

const Z = (z) => (CFG.mirror_z ? -z : z);
const V = (x, y, z) => new THREE.Vector3(x, y, Z(z));
const lerpAngle = (a, b, t) => a + (((b - a + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI) * t;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// ---------- assets ----------
async function loadSponsors() {
  let list = [];
  try { list = await (await fetch(SPONSOR_DIR + "sponsors.json?v=" + SPONSOR_VERSION, { cache: "no-cache" })).json(); } catch { /* fallback */ }
  if (!list.length) list = [{ name: "YOUR SPONSOR", bg: "#ffffff", fg: "#111111" }];
  return Promise.all(list.map(async (s) => {
    let img = null;
    if (s.file) {
      img = await new Promise((res) => {
        const im = new Image();
        im.onload = () => res(im); im.onerror = () => res(null);
        im.src = SPONSOR_DIR + s.file + "?v=" + SPONSOR_VERSION;
      });
    }
    return { ...s, sourceImage: img, board: sponsorTexture(s, img, 1024, 128), roof: sponsorTexture(s, img, 512, 256) };
  }));
}

function sponsorTexture(s, img, w, h, physicalAspect = w / h, copies = 1) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d");
  g.fillStyle = s.bg || "#ffffff"; g.fillRect(0, 0, w, h);
  // Account for the physical panel aspect, not just the atlas cell dimensions.
  const pixelAspect = (w / h) / physicalAspect, cell = w / copies;
  for (let i = 0; i < copies; i++) {
    if (img) {
      const k = Math.min((cell * 0.82) / (img.width * pixelAspect), (h * 0.72) / img.height);
      const dw = img.width * k * pixelAspect, dh = img.height * k;
      g.drawImage(img, i * cell + (cell - dw) / 2, (h - dh) / 2, dw, dh);
    } else {
      g.save(); g.translate((i + 0.5) * cell, h / 2); g.scale(pixelAspect, 1);
      g.fillStyle = s.fg || "#111111";
      g.font = `800 ${Math.floor(h * 0.5)}px "Segoe UI", system-ui, sans-serif`;
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(s.name.toUpperCase(), 0, 0, cell * 0.82 / pixelAspect); g.restore();
    }
  }
  g.fillStyle = s.accent || s.fg || "#152144"; g.fillRect(0, h * 0.94, w, h * 0.06);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

// cfg = settings.toml [models] (car, drone) + [tracks]; entries without a file use procedural models
async function loadModels(cfg = {}) {
  const loader = new GLTFLoader();
  const load = async (entry) => {
    if (!entry?.file) return null;
    try {
      const g = await loader.loadAsync(MODEL_DIR + entry.file);
      const skins = entry.skin_manifest ? await CarSkins.load(MODEL_DIR + entry.skin_manifest) : null;
      return { ...entry, scene: g.scene, animations: g.animations, skins };
    } catch (e) { console.warn("model failed", entry.file, e); return null; }
  };
  const m = cfg.models || {};
  return { car: await load(m.car), drone: await load(m.drone), tracks: cfg.tracks || {}, load };
}

// all sponsors side by side in one texture, so a barrier can be one continuous mesh
function sponsorAtlas(sponsors) {
  const W = 512, H = 128, n = sponsors.length;
  const c = document.createElement("canvas"); c.width = W * n; c.height = H;
  const g = c.getContext("2d");
  sponsors.forEach((s, i) => {
    const aspect = CFG.panel_length_m / CFG.board_height_m;
    const board = sponsorTexture(s, s.sourceImage, W, H, aspect, Math.max(1, Math.round(aspect / 4)));
    g.drawImage(board.image, i * W, 0, W, H); board.dispose();
    g.fillStyle = "rgba(0,0,0,.35)"; g.fillRect(i * W, 0, 3, H);          // panel seams
  });
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace; t.wrapS = THREE.RepeatWrapping; t.anisotropy = 8;
  return t;
}

// ground height: follows the track's elevation near the track and relaxes to a smooth
// average away from it, so the circuit sits on the landscape instead of floating over a plane
function heightField(fr) {
  const pts = [];
  for (let i = 0; i < fr.length; i += 4) pts.push(fr[i].p);
  const mean = pts.reduce((a, p) => a + p.y, 0) / pts.length;
  return (x, z) => {
    let dmin = Infinity, ynear = mean, wsum = 1e-9, ysum = 0;
    for (const p of pts) {
      const d2 = (p.x - x) ** 2 + (p.z - z) ** 2;
      if (d2 < dmin) { dmin = d2; ynear = p.y; }
      const w = 1 / (d2 + 400) ** 1.5;
      wsum += w; ysum += w * p.y;
    }
    const d = Math.sqrt(dmin), far = ysum / wsum;
    const t = clamp((d - 25) / 250, 0, 1), k = t * t * (3 - 2 * t);
    return { y: ynear + (far - ynear) * k, d };
  };
}

function canvasTexture(w, h, draw, repeat) {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat, repeat); }
  return t;
}

function trackFrames(cl, widths) {
  const n = cl.length, out = [];
  for (let i = 0; i < n; i++) {
    const a = cl[(i - 1 + n) % n], b = cl[(i + 1) % n];
    const tx = b[1] - a[1], tz = Z(b[3]) - Z(a[3]);
    const L = Math.hypot(tx, tz) || 1;
    const wl = widths?.[i]?.[0] || DEFAULT_HALF_WIDTH, wr = widths?.[i]?.[1] || DEFAULT_HALF_WIDTH;
    out.push({ p: V(cl[i][1], cl[i][2], cl[i][3]), tx: tx / L, tz: tz / L, nx: -tz / L, nz: tx / L,
               wl: clamp(wl, 3, 15), wr: clamp(wr, 3, 15), heading: Math.atan2(tz, tx) });
  }
  // signed curvature (1/m) from heading change
  for (let i = 0; i < n; i++) {
    const a = out[(i - 1 + n) % n], b = out[(i + 1) % n];
    const ds = a.p.distanceTo(b.p) || 1;
    let dh = b.heading - a.heading;
    dh = ((dh + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    out[i].k = dh / ds;
  }
  return out;
}

// re-pick a drone's watched track point about once a second, not every frame
const now60 = (dr) => { const t = performance.now(); if (!dr.watchAt || t - dr.watchAt > 1000) { dr.watchAt = t; return true; } return false; };

const LIVERY = [0xe8e8e8, 0xd7263d, 0x1b998b, 0x2e86de, 0xf4a259, 0x8e44ad, 0x222222, 0xf1c40f, 0xff7a00, 0x00a19b];

// ---------- view ----------
export class View3D {
  constructor(el, state, ui) {
    this.el = el; this.state = state; this.ui = ui;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = canvasTexture(4, 256, (g, w, h) => {
      const grd = g.createLinearGradient(0, 0, 0, h);
      grd.addColorStop(0, "#4f86c6"); grd.addColorStop(0.55, "#a9cde6"); grd.addColorStop(1, "#dfeaf0");
      g.fillStyle = grd; g.fillRect(0, 0, w, h);
    });
    this.scene.fog = new THREE.Fog(0xcfdfe8, 900, 4200);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.5, 9000);
    this.camera.position.set(0, 600, 900);
    this.camera.layers.enableAll();
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.camMode = "orbit";
    this.selected = 0;                 // selected drone id
    this.clock = new THREE.Clock();

    this.scene.add(new THREE.HemisphereLight(0xdfefff, 0x4a5a3a, 1.0));
    const sun = new THREE.DirectionalLight(0xfff4e0, 1.6);
    sun.position.set(500, 900, 300);
    this.scene.add(sun);

    this.cars = new Map();
    this.drones = new Map();
    this.markers = new Map();
    this.tvCams = [];
    this.ready = loadSponsors().then((s) => { this.sponsors = s; });
    this.bindPointer();
    this.resize();
  }

  resize() {
    const r = this.el.getBoundingClientRect();
    if (!r.width) return;
    this.renderer.setSize(r.width, r.height);
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
  }

  // ---------- track ----------
  resetSession() {
    this.clearCautions();
    this.trackRevision = (this.trackRevision || 0) + 1;
    if (this.trackGroup) this.scene.remove(this.trackGroup);
    this.trackGroup = null;
    for (const c of this.cars.values()) this.scene.remove(c.mesh);
    for (const d of this.drones.values()) this.scene.remove(d.mesh);
    this.cars.clear(); this.drones.clear();
    this.frames = null;
  }

  async setTrack(track) {
    this.clearCautions();
    const revision = this.trackRevision = (this.trackRevision || 0) + 1;
    await this.ready;
    if (revision !== this.trackRevision) return;
    const st = track.settings || {};
    Object.assign(CFG, st.scene || {});
    const models = await loadModels(st);
    if (revision !== this.trackRevision) return;
    this.models?.car?.skins?.dispose();
    this.models = models;
    // anything built before the models arrived is rebuilt with them
    for (const c of this.cars.values()) this.scene.remove(c.mesh);
    for (const d of this.drones.values()) this.scene.remove(d.mesh);
    this.cars.clear(); this.drones.clear();
    if (this.trackGroup) this.scene.remove(this.trackGroup);
    const g = (this.trackGroup = new THREE.Group());
    const cl = track.centerline, fr = (this.frames = trackFrames(cl, track.widths)), n = fr.length;
    this.trackLen = track.length_m;
    const ys = cl.map((p) => p[2]);
    this.minY = Math.min(...ys);

    // grass with a little noise
    const grass = canvasTexture(256, 256, (c, w, h) => {
      c.fillStyle = "#d8e6c8"; c.fillRect(0, 0, w, h);
      for (let i = 0; i < 6000; i++) {
        const v = 170 + Math.random() * 80;
        c.fillStyle = `rgba(${v * 0.85},${v},${v * 0.7},0.5)`;
        c.fillRect(Math.random() * w, Math.random() * h, 2, 3);
      }
    }, 1);
    const hf = (this.height = heightField(fr));
    const xs0 = fr.map((f) => f.p.x), zs0 = fr.map((f) => f.p.z), M = 900;
    const [gx0, gx1, gz0, gz1] = [Math.min(...xs0) - M, Math.max(...xs0) + M, Math.min(...zs0) - M, Math.max(...zs0) + M];
    const cx = (gx0 + gx1) / 2, cz = (gz0 + gz1) / 2;
    const terr = new THREE.PlaneGeometry(gx1 - gx0, gz1 - gz0, CFG.terrain_detail, CFG.terrain_detail);
    terr.rotateX(-Math.PI / 2);
    const tp = terr.attributes.position, tcol = [];
    for (let i = 0; i < tp.count; i++) {
      const x = tp.getX(i) + cx, z = tp.getZ(i) + cz, h = hf(x, z);
      // well under the road right at the track (the verges bridge the gap), flush further out
      tp.setXYZ(i, x, h.y - (h.d < 30 ? 1.2 : 0.4), z);
      const v = 0.85 + 0.15 * Math.sin(x * 0.013) * Math.cos(z * 0.011) + (Math.random() - 0.5) * 0.06;
      tcol.push(0.42 * v, 0.58 * v, 0.32 * v);
    }
    terr.setAttribute("color", new THREE.Float32BufferAttribute(tcol, 3));
    terr.computeVertexNormals();
    grass.repeat.set((gx1 - gx0) / 25, (gz1 - gz0) / 25);
    grass.anisotropy = 8;
    const ground = new THREE.Mesh(terr, new THREE.MeshLambertMaterial({ map: grass, vertexColors: true }));
    g.add(ground);
    const skirt = new THREE.Mesh(new THREE.PlaneGeometry(40000, 40000), new THREE.MeshLambertMaterial({ color: 0x4f6b3e }));
    skirt.rotation.x = -Math.PI / 2; skirt.position.set(cx, this.minY - 40, cz);
    g.add(skirt);

    const edge = (f, side, extra) => {        // point on the left (+1) / right (-1) edge
      const w = (side > 0 ? f.wl : f.wr) + extra;
      return new THREE.Vector3(f.p.x + f.nx * w * side, f.p.y, f.p.z + f.nz * w * side);
    };
    const strip = (pairs, color, y, closed = true, colors = null) => {
      const pos = [], col = [], idx = [];
      pairs.forEach(([a, b], i) => {
        pos.push(a.x, a.y + y, a.z, b.x, b.y + y, b.z);
        if (colors) { const c = colors[i]; col.push(c.r, c.g, c.b, c.r, c.g, c.b); }
      });
      const m = pairs.length;
      for (let i = 0; i < (closed ? m : m - 1); i++) {
        const a = 2 * i, b = 2 * ((i + 1) % m);
        idx.push(a, b, a + 1, a + 1, b, b + 1);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      if (colors) geo.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
      geo.setIndex(idx); geo.computeVertexNormals();
      return new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: colors ? 0xffffff : color, vertexColors: !!colors, side: THREE.DoubleSide }));
    };

    // asphalt + white edge lines
    const asphalt = canvasTexture(128, 128, (c, w, h) => {
      c.fillStyle = "#3a3d40"; c.fillRect(0, 0, w, h);
      for (let i = 0; i < 2500; i++) { const v = 50 + Math.random() * 30; c.fillStyle = `rgba(${v},${v},${v + 4},0.5)`; c.fillRect(Math.random() * w, Math.random() * h, 1, 1); }
    });
    const road = strip(fr.map((f) => [edge(f, 1, 0), edge(f, -1, 0)]), 0x3b3f42, 0.05);
    road.material.map = asphalt; road.material.color.set(0xffffff);
    const uv = []; let acc = 0;
    fr.forEach((f, i) => { if (i) acc += f.p.distanceTo(fr[i - 1].p); uv.push(0, acc / 8, 1, acc / 8); });
    road.geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    asphalt.wrapS = asphalt.wrapT = THREE.RepeatWrapping;
    g.add(road);
    // grass verges: from the track edge down to the terrain, so the road never floats
    const vergeMat = new THREE.MeshLambertMaterial({ color: 0x5d7f47, side: THREE.DoubleSide });
    for (const side of [1, -1]) {
      const pos = [], idx = [];
      fr.forEach((f) => {
        const a = edge(f, side, 0), b = edge(f, side, 16);
        pos.push(a.x, a.y, a.z, b.x, b.y - 1.6, b.z);
      });
      for (let i = 0; i < n; i++) { const a = 2 * i, b = 2 * ((i + 1) % n); idx.push(a, b, a + 1, a + 1, b, b + 1); }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      geo.setIndex(idx); geo.computeVertexNormals();
      g.add(new THREE.Mesh(geo, vergeMat));
    }
    this.proceduralTrack = [ground, road];
    for (const side of [1, -1]) {
      const line = strip(fr.map((f) => [edge(f, side, -0.6), edge(f, side, -0.3)]), 0xf2f2f2, 0.08);
      g.add(line); this.proceduralTrack.push(line);
    }

    // kerbs + run-off where the corner is tight
    const corner = fr.map((f) => Math.abs(f.k) > 1 / CFG.kerb_radius_m);
    const grow = corner.map((c, i) => c || corner[(i + 3) % n] || corner[(i - 3 + n) % n]);
    const red = new THREE.Color(0xd12b2b), white = new THREE.Color(0xf4f4f4), sand = new THREE.Color(0xc9b27c);
    for (const side of [1, -1]) {
      let run = [];
      const flush = () => {
        if (run.length > 2) {
          const kerb = strip(run.map((i) => [edge(fr[i], side, 0), edge(fr[i], side, 1.3)]), 0, 0.07, false,
            run.map((i) => ((i >> 1) % 2 ? red : white)));
          const off = strip(run.map((i) => [edge(fr[i], side, 1.3), edge(fr[i], side, 9)]), 0, 0.03, false,
            run.map(() => sand));
          g.add(kerb, off);
        }
        run = [];
      };
      for (let i = 0; i < n; i++) { if (grow[i]) run.push(i); else flush(); }
      flush();
    }

    // sponsor boards: one continuous barrier per side, pushed back behind run-off in corners
    // (smoothly), and left out on the inside of corners too tight for the offset to stay clean
    const atlas = sponsorAtlas(this.sponsors);
    const nS = this.sponsors.length;
    const frontMat = new THREE.MeshBasicMaterial({ map: atlas });
    const backMat = new THREE.MeshStandardMaterial({ color: CFG.railing_color, roughness: 0.58, metalness: 0.45, side: THREE.DoubleSide });
    const supportPositions = [];
    const depth = Math.max(0.08, CFG.railing_depth_m);
    const postSpacing = Math.max(1, CFG.railing_post_spacing_m);
    const raw = grow.map((c) => (c ? 10 : 0));
    const smooth = raw.map((_, i) => { let a = 0; for (let j = -12; j <= 12; j++) a += raw[(i + j + n) % n]; return a / 25; });
    for (const side of [1, -1]) {
      let run = [];
      const flush = () => {
        if (run.length > 3) {
          const pos = [], outer = [], back = [], caps = [], uvs = [], outerUvs = [], idx = [], backIdx = [];
          const profile = [0, 0.12, 0.28, 0.38, 0.5, 0.62, 0.78, 0.88, 1];
          let dist = 0, prev = null, nextPost = 0;
          run.forEach((i) => {
            const extra = CFG.board_gap_m + smooth[i];
            const p = edge(fr[i], side, extra), q = edge(fr[i], side, extra + depth);
            if (prev) dist += p.distanceTo(prev);
            prev = p;
            const y0 = p.y + 0.15, y1 = y0 + CFG.board_height_m;
            pos.push(p.x, y0, p.z, p.x, y1, p.z);
            // Printed outer fascia clears the corrugations and remains visible from orbit.
            const ox = q.x + fr[i].nx * side * 0.07, oz = q.z + fr[i].nz * side * 0.07;
            outer.push(ox, y0, oz, ox, y1, oz);
            profile.forEach((height, j) => {
              const ridge = j % 2 ? 0.05 : 0;
              back.push(q.x + fr[i].nx * side * ridge, y0 + height * CFG.board_height_m, q.z + fr[i].nz * side * ridge);
            });
            caps.push(p.x, y1, p.z, q.x, y1, q.z);
            if (dist >= nextPost || i === run[run.length - 1]) {
              const post = edge(fr[i], side, extra + depth + 0.08);
              supportPositions.push({ p: post, heading: fr[i].heading });
              nextPost = dist + postSpacing;
            }
            // text must read left-to-right from the track: flip it on the side whose normal faces away
            const u = (side > 0 ? -dist : dist) / (CFG.panel_length_m * nS);
            uvs.push(u, 0, u, 1);
            outerUvs.push(-u, 0, -u, 1);
          });
          for (let j = 0; j < run.length - 1; j++) {
            const a = 2 * j, b = a + 2;
            // Inner and outer faces use opposite winding and UV direction.
            if (side > 0) idx.push(a, a + 1, b, a + 1, b + 1, b);
            else idx.push(a, b, a + 1, a + 1, b, b + 1);
            for (let k = 0; k < profile.length - 1; k++) {
              const c = j * profile.length + k, d = c + profile.length;
              backIdx.push(c, d, c + 1, c + 1, d, d + 1);
            }
          }
          const outerIdx = [];
          for (let j = 0; j < idx.length; j += 3) outerIdx.push(idx[j], idx[j + 2], idx[j + 1]);
          const outerGeo = new THREE.BufferGeometry();
          outerGeo.setAttribute("position", new THREE.Float32BufferAttribute(outer, 3));
          outerGeo.setAttribute("uv", new THREE.Float32BufferAttribute(outerUvs, 2));
          outerGeo.setIndex(outerIdx); outerGeo.computeVertexNormals();
          const outerMesh = new THREE.Mesh(outerGeo, frontMat);
          outerMesh.name = 'Outer sponsor panels'; g.add(outerMesh);
          for (const [arr, mat, indices, name] of [[pos, frontMat, idx, 'Sponsor panels'], [back, backMat, backIdx, 'Corrugated steel railing'], [caps, backMat, idx, 'Railing top cap']]) {
            const geo = new THREE.BufferGeometry();
            geo.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
            if (mat === frontMat) geo.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
            geo.setIndex(indices); geo.computeVertexNormals();
            const mesh = new THREE.Mesh(geo, mat); mesh.name = name; g.add(mesh);
          }
        }
        run = [];
      };
      for (let i = 0; i < n; i++) {
        const extra = CFG.board_gap_m + smooth[i] + (side > 0 ? fr[i].wl : fr[i].wr);
        const inside = Math.sign(fr[i].k) === side;          // curving towards this side
        if (inside && Math.abs(fr[i].k) * extra > 0.35) flush(); else run.push(i);
      }
      flush();
    }
    if (supportPositions.length) {
      const height = CFG.board_height_m + 0.45;
      const posts = new THREE.InstancedMesh(new THREE.BoxGeometry(0.14, height, 0.16), backMat, supportPositions.length);
      posts.name = 'Railing support posts';
      const pose = new THREE.Object3D();
      supportPositions.forEach(({ p, heading }, i) => {
        pose.position.set(p.x, p.y - 0.2 + height / 2, p.z); pose.rotation.y = -heading;
        pose.updateMatrix(); posts.setMatrixAt(i, pose.matrix);
      });
      posts.instanceMatrix.needsUpdate = true; g.add(posts);
    }
    const postMat = new THREE.MeshLambertMaterial({ color: 0x9aa3a8 });

    // start/finish: chequered line, gantry with the first sponsor, grandstand
    const f0 = fr[0];
    const cheq = canvasTexture(64, 16, (c, w, h) => { for (let x = 0; x < 16; x++) for (let y = 0; y < 4; y++) { c.fillStyle = (x + y) % 2 ? "#111" : "#fff"; c.fillRect(x * 4, y * 4, 4, 4); } });
    const line = new THREE.Mesh(new THREE.PlaneGeometry(f0.wl + f0.wr, 2), new THREE.MeshBasicMaterial({ map: cheq }));
    line.rotation.x = -Math.PI / 2; line.rotation.z = f0.heading + Math.PI / 2;
    line.position.copy(f0.p).add(new THREE.Vector3(0, 0.09, 0));
    g.add(line);
    const gantry = new THREE.Group();
    const w = f0.wl + f0.wr + 4;
    for (const s of [-1, 1]) {
      const post = new THREE.Mesh(new THREE.BoxGeometry(0.6, 8, 0.6), postMat);
      post.position.set(0, 4, s * w / 2); gantry.add(post);
    }
    const beam = new THREE.Mesh(new THREE.BoxGeometry(1, 1.6, w), new THREE.MeshLambertMaterial({ color: 0x22262a }));
    beam.position.y = 8; gantry.add(beam);
    const gantryTexture = sponsorTexture(this.sponsors[0], this.sponsors[0].sourceImage, 1024, 128, (w - 1) / 1.3, 2);
    for (const s of [-1, 1]) {
      const sign = new THREE.Mesh(new THREE.PlaneGeometry(w - 1, 1.3), new THREE.MeshBasicMaterial({ map: gantryTexture }));
      sign.position.set(s * 0.51, 8, 0); sign.rotation.y = s * Math.PI / 2; gantry.add(sign);
    }
    gantry.position.copy(f0.p);
    gantry.rotation.y = -f0.heading;
    g.add(gantry);
    const stand = new THREE.Group();
    const standMat = new THREE.MeshLambertMaterial({ color: 0x8c96a0 });
    const crowd = canvasTexture(256, 32, (c, W, H) => { for (let i = 0; i < 900; i++) { c.fillStyle = `hsl(${Math.random() * 360},60%,${40 + Math.random() * 30}%)`; c.fillRect(Math.random() * W, Math.random() * H, 3, 3); } });
    for (let t = 0; t < 4; t++) {
      const tier = new THREE.Mesh(new THREE.BoxGeometry(90, 1.5, 4), [standMat, standMat, new THREE.MeshLambertMaterial({ map: crowd }), standMat, standMat, standMat]);
      tier.position.set(0, 0.75 + t * 1.5, t * 4); stand.add(tier);
    }
    const roof = new THREE.Mesh(new THREE.BoxGeometry(92, 0.4, 18), new THREE.MeshLambertMaterial({ color: 0xe8e8e8 }));
    roof.position.set(0, 10, 6); stand.add(roof);
    const side = 1;
    stand.position.copy(edge(f0, side, 22));
    stand.rotation.y = -f0.heading;
    if (side < 0) stand.rotation.y += Math.PI;
    g.add(stand);

    // trees (instanced), kept well away from the track
    const far = [];
    for (let i = 0; i < n; i += 6) far.push(fr[i].p);
    const xs = far.map((p) => p.x), zs = far.map((p) => p.z);
    const [x0, x1, z0, z1] = [Math.min(...xs) - 400, Math.max(...xs) + 400, Math.min(...zs) - 400, Math.max(...zs) + 400];
    const trunkGeo = new THREE.CylinderGeometry(0.4, 0.5, 4, 6), crownGeo = new THREE.ConeGeometry(3.2, 9, 8);
    const trunks = new THREE.InstancedMesh(trunkGeo, new THREE.MeshLambertMaterial({ color: 0x5a4030 }), 900);
    const crowns = new THREE.InstancedMesh(crownGeo, new THREE.MeshLambertMaterial({ color: 0x2f5a2c }), 900);
    const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3();
    let placed = 0;
    for (let tries = 0; tries < 6000 && placed < 900; tries++) {
      const x = x0 + Math.random() * (x1 - x0), z = z0 + Math.random() * (z1 - z0);
      const h = hf(x, z);
      if (h.d < 45) continue;
      const py = h.y + 0.4;
      const s = 0.7 + Math.random() * 0.8;
      sc.set(s, s, s);
      m4.compose(new THREE.Vector3(x, py - 0.8 + 2 * s, z), q, sc); trunks.setMatrixAt(placed, m4);
      m4.compose(new THREE.Vector3(x, py - 0.8 + 8 * s, z), q, sc); crowns.setMatrixAt(placed, m4);
      placed++;
    }
    trunks.count = crowns.count = placed;
    g.add(trunks, crowns);

    // trackside TV cameras
    this.tvCams = [];
    const tvStep = Math.max(1, Math.round((CFG.tv_camera_spacing_m / track.length_m) * n));
    for (let i = 0; i < n; i += tvStep) this.tvCams.push(edge(fr[i], (i / tvStep) % 2 ? 1 : -1, 28).add(new THREE.Vector3(0, 9, 0)));

    this.scene.add(g);
    this.center = new THREE.Box3().setFromObject(road).getCenter(new THREE.Vector3());
    this.controls.target.copy(this.center);
    this.camera.position.set(this.center.x, this.center.y + 900, this.center.z + 1100);
    await this.loadCustomTrack(track.track_id, revision);
  }

  async loadCustomTrack(id, revision) {
    const entry = this.models?.tracks?.[id];
    if (!entry) return;
    const m = await this.models.load(entry);
    if (!m || revision !== this.trackRevision) return;
    const obj = m.scene;
    obj.scale.setScalar(entry.scale || 1);
    obj.rotation.y = entry.rotation_y || 0;
    const [ox, oy, oz] = entry.offset || [0, 0, 0];
    obj.position.set(ox, oy, oz);
    this.trackGroup.add(obj);
    if (entry.hide_procedural !== false) this.proceduralTrack.forEach((o) => (o.visible = false));
    console.info("custom track model loaded:", id);
  }

  // ---------- cars ----------
  makeCar(id) {
    const root = new THREE.Group();
    const cfg = this.models?.car;
    const wheels = [];
    const skinParts = [];
    if (cfg) {
      const obj = cloneSkinned(cfg.scene);
      obj.scale.setScalar(cfg.scale || 1);
      obj.rotation.y = cfg.rotation_y || 0;
      obj.position.y = cfg.y_offset || 0;
      obj.traverse(o => { if (o.isMesh) skinParts.push({mesh: o, base: o.material}); });
      // glTF splits multi-material wheels into children. Spin the pivot once,
      // never its matching descendants as well (which would double the rotation).
      const wheelName = /wheel|tyre|tire/i;
      obj.traverse((o) => {
        if (!wheelName.test(o.name)) return;
        for (let p = o.parent; p && p !== obj; p = p.parent) {
          if (wheelName.test(p.name)) return;
        }
        wheels.push(o);
      });
      root.add(obj);
    } else {
      const livery = new THREE.MeshLambertMaterial({ color: LIVERY[id % LIVERY.length] });
      const dark = new THREE.MeshLambertMaterial({ color: 0x15171a });
      const add = (geo, mat, x, y, z) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); root.add(m); return m; };
      add(new THREE.BoxGeometry(3.4, 0.5, 1.5), livery, -0.2, 0.45, 0);            // body
      add(new THREE.BoxGeometry(1.5, 0.3, 0.7), livery, 2.1, 0.35, 0);              // nose
      add(new THREE.BoxGeometry(1.1, 0.45, 0.9), dark, -0.3, 0.9, 0);               // cockpit / engine cover
      add(new THREE.BoxGeometry(0.35, 0.06, 1.9), dark, 2.85, 0.18, 0);             // front wing
      add(new THREE.BoxGeometry(0.45, 0.07, 1.6), livery, -2.1, 1.15, 0);           // rear wing
      for (const s of [-1, 1]) add(new THREE.BoxGeometry(0.5, 0.55, 0.05), dark, -2.1, 0.9, s * 0.8);
      const wheelGeo = new THREE.CylinderGeometry(0.36, 0.36, 0.38, 14);
      wheelGeo.rotateX(Math.PI / 2);
      for (const [x, z] of [[1.55, 0.85], [1.55, -0.85], [-1.45, 0.85], [-1.45, -0.85]]) wheels.push(add(wheelGeo, dark, x, 0.36, z));
      if (this.sponsors?.length) {
        const s = this.sponsors[id % this.sponsors.length];
        const decal = add(new THREE.PlaneGeometry(1.4, 1.0), new THREE.MeshBasicMaterial({ map: s.roof }), 0.9, 0.71, 0);
        decal.rotation.x = -Math.PI / 2; decal.rotation.z = -Math.PI / 2;
      }
    }
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(5.2, 2.2), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.28, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.06; root.add(shadow);
    const player = id === CFG.player_car_id;
    const label = this.makeLabel(player ? `YOU · #${id}` : `#${id}`);
    label.position.y = player ? 5.2 : 3.2;
    root.add(label);
    if (player) {
      const marker = new THREE.Mesh(new THREE.ConeGeometry(0.65, 1.6, 4),
        new THREE.MeshBasicMaterial({ color: 0xff3030, depthTest: false, depthWrite: false }));
      marker.rotation.z = Math.PI;
      marker.position.y = 3.5;
      marker.renderOrder = 11;
      root.add(marker);
    }
    root.scale.setScalar(CFG.car_scale);
    this.scene.add(root);
    return { mesh: root, wheels, label, yaw: 0, skinParts };
  }

  makeLabel(text) {
    const tex = canvasTexture(128, 48, (c, w, h) => {
      c.fillStyle = "rgba(13,18,20,0.75)"; c.beginPath(); c.roundRect(2, 2, w - 4, h - 4, 10); c.fill();
      c.fillStyle = "#fff"; c.font = "700 28px Segoe UI, sans-serif"; c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(text, w / 2, h / 2 + 1);
    });
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    s.scale.set(3, 1.1, 1);
    s.renderOrder = 10;
    return s;
  }

  // ---------- drones ----------
  makeDrone(d) {
    const col = d.pilot === "fly" ? 0xf2b729 : 0x58a6ff;
    const root = new THREE.Group();          // position + yaw
    const body = new THREE.Group();          // pitch/roll tilt
    root.add(body);
    const rotors = [];
    let mixer = null;
    const cfg = this.models?.drone;
    if (cfg) {
      const obj = cloneSkinned(cfg.scene);
      obj.scale.setScalar(cfg.scale || 1);
      obj.rotation.y = cfg.rotation_y || 0;
      const re = new RegExp(cfg.rotor_nodes || "rotor|prop", "i");
      obj.traverse((o) => { if (re.test(o.name)) rotors.push(o); });
      if (cfg.animations?.length) { mixer = new THREE.AnimationMixer(obj); cfg.animations.forEach((a) => mixer.clipAction(a).play()); }
      body.add(obj);
    } else {
      const mat = new THREE.MeshLambertMaterial({ color: col });
      const dark = new THREE.MeshLambertMaterial({ color: 0x1c1f22 });
      const hull = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.22, 0.5), mat); body.add(hull);
      for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
        const arm = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.06, 0.08), dark);
        arm.position.set(x * 0.4, 0.05, z * 0.4); arm.rotation.y = Math.atan2(-z, x); body.add(arm);
        const hub = new THREE.Group(); hub.position.set(x * 0.72, 0.16, z * 0.72); body.add(hub);
        const blades = new THREE.Mesh(new THREE.BoxGeometry(0.75, 0.015, 0.07), dark); hub.add(blades);
        const blur = new THREE.Mesh(new THREE.CircleGeometry(0.4, 20), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
        blur.rotation.x = -Math.PI / 2; hub.add(blur);
        rotors.push(hub);
      }
      const gimbal = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 8), dark); gimbal.position.set(0.25, -0.18, 0); body.add(gimbal);
      for (const [z, c] of [[0.3, 0xff3030], [-0.3, 0x30ff60]]) {
        const led = new THREE.Mesh(new THREE.SphereGeometry(0.05, 8, 6), new THREE.MeshBasicMaterial({ color: c }));
        led.position.set(-0.36, 0.02, z); led.userData.led = true; body.add(led);
      }
    }
    const cone = new THREE.Mesh(new THREE.ConeGeometry(4, 10, 24, 1, true),
      new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
    cone.position.y = -5.2;
    root.add(cone);
    root.scale.setScalar(CFG.drone_scale);
    // own layer so its gimbal camera doesn't see its own body
    const layer = 1 + d.drone_id;
    root.traverse((o) => o.layers.set(layer));
    const label = this.makeLabel(`${d.pilot === "fly" ? "FLY" : "D"}${d.drone_id}`);
    label.position.y = 1.4; label.scale.set(1.2, 0.45, 1); label.layers.set(layer); root.add(label);
    this.scene.add(root);

    const cam = new THREE.PerspectiveCamera(55, 16 / 9, 0.3, 5000);
    cam.layers.enableAll(); cam.layers.disable(layer);
    return { mesh: root, body, rotors, mixer, cone, label, cam, yaw: 0, gimbal: { yaw: 0, pitch: -0.5, yawOff: 0, pitchOff: 0, fov: 55, manual: false } };
  }

  // ---------- interaction ----------
  bindPointer() {
    const dom = this.renderer.domElement;
    let drag = null;
    dom.addEventListener("pointerdown", (e) => { if (this.camMode === "drone") { drag = { x: e.clientX, y: e.clientY }; dom.setPointerCapture(e.pointerId); } });
    dom.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dr = this.drones.get(this.selected); if (!dr) return;
      const g = dr.gimbal;
      g.manual = true;
      g.yawOff -= (e.clientX - drag.x) * 0.004 * (g.fov / 55);
      g.pitchOff -= (e.clientY - drag.y) * 0.004 * (g.fov / 55);
      drag = { x: e.clientX, y: e.clientY };
    });
    dom.addEventListener("pointerup", () => (drag = null));
    dom.addEventListener("wheel", (e) => {
      if (this.camMode !== "drone") return;
      const dr = this.drones.get(this.selected); if (!dr) return;
      e.preventDefault();
      dr.gimbal.fov = clamp(dr.gimbal.fov * (e.deltaY > 0 ? 1.1 : 0.9), 8, 80);
    }, { passive: false });
    dom.addEventListener("dblclick", () => {
      const dr = this.drones.get(this.selected);
      if (dr && this.camMode === "drone") Object.assign(dr.gimbal, { yawOff: 0, pitchOff: 0, fov: 55, manual: false });
    });
  }

  setCamera(mode) {
    this.camMode = mode;
    this.controls.enabled = mode === "orbit" || mode === "chase";
    if (mode === "chase") {
      const dr = this.drones.get(this.selected);
      if (dr) {
        this.controls.target.copy(dr.mesh.position);
        this.camera.position.copy(dr.mesh.position).add(new THREE.Vector3(-40, 20, 40));
      }
    }
    if (mode === "orbit" && this.center) this.controls.target.copy(this.center);
  }

  selectDrone(id) { this.selected = id; if (this.camMode === "chase") this.setCamera("chase"); }

  // ---------- per frame ----------
  focusPoint() {
    const s = this.state;
    const inc = [...s.events.values()].find((e) => e.type === "incident") || [...s.events.values()][0];
    if (inc) {
      const car = this.cars.get(inc.car_ids[0]);
      const ev = V(inc.x, inc.y, inc.z);
      return car && car.mesh.position.distanceTo(ev) < 400 ? car.mesh.position.clone() : ev;
    }
    const leader = [...s.cars.values()].sort((a, b) => (b.lap + b.track_pos) - (a.lap + a.track_pos))[0];
    return leader ? this.cars.get(leader.car_id)?.mesh.position.clone() : this.center?.clone();
  }

  clearCautions() {
    if (this.cautionGroup) {
      this.scene.remove(this.cautionGroup);
      this.cautionGroup.traverse(o => {
        o.geometry?.dispose();
        if (o.material) { o.material.map?.dispose(); o.material.dispose(); }
      });
    }
    this.cautionGroup = null; this.cautionKey = null;
  }

  updateCautions(now) {
    const track = this.state.track;
    if (!track || !this.frames) { this.clearCautions(); return; }
    if (now < (this.nextCautionUpdate || 0)) return;
    this.nextCautionUpdate = now + 200;
    const ranges = track.settings?.scene?.yellow_3d_enabled === false ? []
      : cautionRanges(this.state.events.values(), track.length_m, track.settings?.scene);
    const key = JSON.stringify(ranges);
    if (key === this.cautionKey) return;
    this.clearCautions(); this.cautionKey = key;
    if (!ranges.length) return;
    const group = this.cautionGroup = new THREE.Group();
    this.scene.add(group);
    const cl = track.centerline, frames = this.frames, vertices = [];
    const edge = (i, f, side, offset) => {
      const a = frames[i], b = frames[(i + 1) % frames.length];
      const aw = (side > 0 ? a.wl : a.wr) + offset;
      const bw = (side > 0 ? b.wl : b.wr) + offset;
      return [a.p.x + side*a.nx*aw + f*(b.p.x + side*b.nx*bw - a.p.x - side*a.nx*aw),
        a.p.y + f*(b.p.y-a.p.y) + .35,
        a.p.z + side*a.nz*aw + f*(b.p.z + side*b.nz*bw - a.p.z - side*a.nz*aw)];
    };
    const at = (t, side, offset) => {
      const i = Math.max(0, cl.findLastIndex(p => p[0] <= t));
      const end = i+1 === cl.length ? 1 : cl[i+1][0];
      return edge(i, (t-cl[i][0]) / Math.max(1e-9, end-cl[i][0]), side, offset);
    };
    for (let i=0; i<cl.length; i++) {
      const start = cl[i][0], end = i+1 === cl.length ? 1 : cl[i+1][0];
      for (const [lo, hi] of ranges) {
        const a = Math.max(start, lo), b = Math.min(end, hi);
        if (b <= a) continue;
        for (const side of [-1, 1]) {
          const u=(a-start)/(end-start), v=(b-start)/(end-start);
          const p=edge(i,u,side,.5), q=edge(i,u,side,2.5), r=edge(i,v,side,.5), s=edge(i,v,side,2.5);
          vertices.push(...p,...q,...r,...q,...s,...r);
        }
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices,3));
    group.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({color:0xffd43b,
      transparent:true, opacity:.65, depthWrite:false, side:THREE.DoubleSide})));
    const wraps = ranges.length > 1 && ranges[0][0] === 0 && ranges.at(-1)[1] === 1;
    for (const [start,end] of ranges) {
      if (start===0 && end===1) continue;
      for (const [t,entry] of [[start,true],[end,false]]) {
        if (wraps && (t===0 || t===1)) continue; // not a real boundary at start/finish
        const pos=at(t,1,3), color=entry ? 0xffd43b : 0x54dc90;
        const post=new THREE.Mesh(new THREE.CylinderGeometry(.3,.3,4,6),new THREE.MeshBasicMaterial({color}));
        post.position.set(pos[0],pos[1]+2,pos[2]); group.add(post);
        const label=this.makeLabel(entry ? 'YELLOW' : 'END');
        label.position.set(pos[0],pos[1]+5,pos[2]); label.scale.set(10,3.5,1);
        label.material.color.setHex(color); group.add(label);
      }
    }
  }

  update() {
    const s = this.state;
    const dt = Math.min(0.1, this.clock.getDelta());
    const now = performance.now();
    this.updateCautions(now);
    if (!this.sponsors) return;

    // cars: interpolate between the last two telemetry ticks
    const aC = clamp((now - s.frameAt) / (s.frameDt || 66), 0, 1);
    for (const c of s.cars.values()) {
      let car = this.cars.get(c.car_id);
      if (!car) { car = this.makeCar(c.car_id); this.cars.set(c.car_id, car); }
      this.models?.car?.skins?.apply(car, c.car_model, c.skin);
      const p0 = s.prevCars.get(c.car_id) || c;
      const target = V(p0.x + (c.x - p0.x) * aC, p0.y + (c.y - p0.y) * aC, p0.z + (c.z - p0.z) * aC);
      const dx = c.x - p0.x, dz = Z(c.z) - Z(p0.z);
      if (dx * dx + dz * dz > 0.01) car.yaw = lerpAngle(car.yaw, Math.atan2(dz, dx), 0.25);
      car.mesh.position.copy(target);
      car.mesh.rotation.y = -car.yaw;
      const spin = (c.speed_kmh / 3.6) / 0.36 * dt;
      car.wheels.forEach((w) => (w.rotation.z -= spin));
      car.label.visible = this.ui.labels;
      car.mesh.visible = c.car_id === CFG.player_car_id || !c.in_pit || c.speed_kmh > 1;
    }

    // drones: interpolate, face travel direction, tilt with speed and turn rate, spin rotors
    const aD = clamp((now - s.dronesAt) / (s.dronesDt || 66), 0, 1);
    for (const d of s.drones) {
      let dr = this.drones.get(d.drone_id);
      if (!dr) { dr = this.makeDrone(d); this.drones.set(d.drone_id, dr); }
      const p0 = s.prevDrones.get(d.drone_id) || d;
      dr.mesh.position.copy(V(p0.x + (d.x - p0.x) * aD, p0.y + (d.y - p0.y) * aD, p0.z + (d.z - p0.z) * aD));
      const sp = Math.hypot(d.vx, d.vz);
      const prevYaw = dr.yaw;
      if (sp > 0.8) dr.yaw = lerpAngle(dr.yaw, Math.atan2(Z(d.vz), d.vx), 0.12);
      const yawRate = (dr.yaw - prevYaw) / Math.max(dt, 1e-3);
      dr.mesh.rotation.y = -dr.yaw;
      dr.body.rotation.z = lerpAngle(dr.body.rotation.z, -clamp(sp / 25, 0, 1) * 0.35, 0.1);       // nose down
      dr.body.rotation.x = lerpAngle(dr.body.rotation.x, clamp(yawRate * 0.25, -0.35, 0.35), 0.1);  // bank
      dr.rotors.forEach((r, i) => (r.rotation.y += (i % 2 ? -1 : 1) * (60 + sp * 2) * dt));
      dr.mixer?.update(dt);
      dr.body.traverse((o) => { if (o.userData.led) o.visible = (now / 500 | 0) % 2 === 0; });
      dr.cone.material.opacity = d.mode === "patrol" ? 0.07 : 0.2;
      dr.label.visible = this.ui.labels;
      dr.state = d;
      this.aimGimbal(dr, d, dt);
    }

    // event markers
    const pulse = 1 + 0.25 * Math.sin(now / 200);
    for (const [id, m] of this.markers) if (!s.events.has(id)) { this.scene.remove(m); this.markers.delete(id); }
    for (const e of s.events.values()) {
      let m = this.markers.get(e.id);
      if (!m) {
        m = new THREE.Mesh(new THREE.RingGeometry(10, 13, 48), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false }));
        m.rotation.x = -Math.PI / 2;
        this.scene.add(m); this.markers.set(e.id, m);
      }
      m.position.copy(V(e.x, e.y + 0.3, e.z));
      m.scale.setScalar(pulse);
      m.material.color.setHex(e.type === "incident" ? 0xff5a4f : 0xf2b729);
    }

    this.updateCamera(dt);
    this.render();
  }

  aimGimbal(dr, d, dt) {
    const s = this.state;
    const camPos = dr.mesh.position.clone().add(new THREE.Vector3(0, -0.6 * CFG.drone_scale, 0));
    let look = null;
    const ev = d.event_id && s.events.get(d.event_id);
    if (ev) {
      const car = this.cars.get(ev.car_ids[0]);
      const evp = V(ev.x, ev.y, ev.z);
      look = car && car.mesh.position.distanceTo(evp) < 400 ? car.mesh.position.clone() : evp;
    } else if (this.frames) {
      // patrol: watch the nearest stretch of track, a little upstream so approaching cars are in shot
      const fr = this.frames, n = fr.length;
      if (dr.watch === undefined || now60(dr)) {
        let best = Infinity, bi = 0;
        for (let i = 0; i < n; i += 3) { const q = fr[i].p; const dd = (q.x - camPos.x) ** 2 + (q.z - camPos.z) ** 2; if (dd < best) { best = dd; bi = i; } }
        dr.watch = bi;
      }
      const up = Math.max(1, Math.round(n * 60 / (this.trackLen || 5000)));   // ~60 m upstream
      look = fr[(dr.watch - up + n) % n].p.clone();
    } else {
      look = camPos.clone().add(new THREE.Vector3(Math.cos(dr.yaw) * 60, -25, Math.sin(dr.yaw) * 60));
    }
    const dir = look.sub(camPos);
    const g = dr.gimbal;
    const yaw = Math.atan2(dir.z, dir.x) + g.yawOff;
    const pitch = clamp(Math.atan2(dir.y, Math.hypot(dir.x, dir.z)) + g.pitchOff, -1.5, 0.35);
    g.yaw = lerpAngle(g.yaw, yaw, 1 - Math.exp(-dt * 4));         // gimbal slew
    g.pitch += (pitch - g.pitch) * (1 - Math.exp(-dt * 4));
    const shake = Math.hypot(d.vx, d.vz) / 25 * 0.004;
    const cy = Math.cos(g.pitch);
    dr.cam.position.copy(camPos);
    dr.cam.lookAt(camPos.x + Math.cos(g.yaw + (Math.random() - 0.5) * shake) * cy,
                  camPos.y + Math.sin(g.pitch + (Math.random() - 0.5) * shake),
                  camPos.z + Math.sin(g.yaw) * cy);
    dr.cam.fov += (g.fov - dr.cam.fov) * 0.2;
    dr.cam.updateProjectionMatrix();
  }

  updateCamera(dt) {
    const mode = this.camMode;
    const dr = this.drones.get(this.selected);
    this.camera.layers.enableAll();
    if (mode === "drone") this.camera.layers.disable(1 + this.selected);   // don't render its own body
    if (mode === "chase" && dr) {
      const delta = dr.mesh.position.clone().sub(this.controls.target);
      this.controls.target.add(delta);
      this.camera.position.add(delta);
      this.controls.update();
    } else if (mode === "drone" && dr) {
      this.camera.position.copy(dr.cam.position);
      this.camera.quaternion.copy(dr.cam.quaternion);
      this.camera.fov = dr.cam.fov;
      this.camera.updateProjectionMatrix();
    } else if (mode === "tv" || mode === "incident") {
      const f = this.focusPoint();
      if (f) {
        let pos;
        if (mode === "tv" && this.tvCams.length) {
          pos = this.tvCams.reduce((best, c) => (c.distanceTo(f) < best.distanceTo(f) ? c : best));
        } else {
          pos = f.clone().add(new THREE.Vector3(45, 35, 45));
        }
        this.camera.position.lerp(pos, mode === "tv" ? 0.5 : 0.05);
        this.tvLook = this.tvLook ? this.tvLook.lerp(f, 0.15) : f.clone();
        this.camera.lookAt(this.tvLook);
        const dist = this.camera.position.distanceTo(f);
        this.camera.fov += (clamp(THREE.MathUtils.radToDeg(2 * Math.atan(22 / dist)), 6, 50) - this.camera.fov) * 0.1;
        this.camera.updateProjectionMatrix();
      }
    } else {
      if (this.camera.fov !== 50) { this.camera.fov = 50; this.camera.updateProjectionMatrix(); }
      this.controls.update();
    }
  }

  render() {
    const r = this.renderer, size = r.getSize(new THREE.Vector2());
    r.setScissorTest(false);
    r.setViewport(0, 0, size.x, size.y);
    r.render(this.scene, this.camera);
    // drone feed tiles, drawn into the same canvas under their HTML frames
    if (!this.ui.feeds || !this.ui.tiles) return;
    const box = r.domElement.getBoundingClientRect();
    r.setScissorTest(true);
    for (const tile of this.ui.tiles.querySelectorAll("[data-drone]")) {
      if (tile.dataset.source === 'game') continue;
      const dr = this.drones.get(+tile.dataset.drone);
      if (!dr) continue;
      const t = tile.querySelector(".feed").getBoundingClientRect();
      const x = t.left - box.left, y = box.bottom - t.bottom;
      if (t.width < 2 || x < 0 || y < 0) continue;
      dr.cam.aspect = t.width / t.height;
      dr.cam.updateProjectionMatrix();
      r.setViewport(x, y, t.width, t.height);
      r.setScissor(x, y, t.width, t.height);
      r.render(this.scene, dr.cam);
    }
    r.setScissorTest(false);
  }

  // data the HUD and minimap need
  hudInfo() {
    const dr = this.drones.get(this.selected);
    if (!dr?.state) return null;
    const d = dr.state, fr = this.frames;
    let ground = this.minY;
    if (fr) { let best = Infinity; for (let i = 0; i < fr.length; i += 4) { const q = fr[i].p; const dd = (q.x - d.x) ** 2 + (q.z - Z(d.z)) ** 2; if (dd < best) { best = dd; ground = q.y; } } }
    return { d, alt: d.y - ground, speed: Math.hypot(d.vx, d.vy, d.vz) * 3.6, fov: dr.cam.fov, gimbal: dr.gimbal };
  }
}
