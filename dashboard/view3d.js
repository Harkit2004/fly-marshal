// 3D view (Three.js). Same data as the 2D map: centreline -> track ribbon and
// sponsor barriers, car frames -> car blocks with sponsor roof decals,
// drone states -> drones with camera cones.
//
// AC and Three.js are both y-up. If the track appears mirrored compared with
// the 2D map, set MIRROR_Z = true.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const MIRROR_Z = false;
const TRACK_HALF_WIDTH = 6;      // m
const BARRIER_OFFSET = 10;       // m from centreline
const BARRIER_HEIGHT = 1.2;
const PANEL_LEN = 14;            // m per sponsor board
const DRONE_SCALE = 3;           // drones are tiny at track scale; exaggerate for visibility
const SPONSOR_DIR = "assets/sponsors/";

const Z = (z) => (MIRROR_Z ? -z : z);

// ---------- sponsor textures ----------
async function loadSponsors() {
  let list = [];
  try { list = await (await fetch(SPONSOR_DIR + "sponsors.json")).json(); } catch { /* use fallback */ }
  if (!list.length) list = [{ name: "YOUR SPONSOR", bg: "#ffffff", fg: "#111111" }];
  return Promise.all(list.map(async (s) => {
    let img = null;
    if (s.file) {
      img = await new Promise((res) => {
        const im = new Image();
        im.onload = () => res(im); im.onerror = () => res(null);
        im.src = SPONSOR_DIR + s.file;
      });
    }
    return { ...s, img, board: sponsorTexture(s, img, 1024, 96), roof: sponsorTexture(s, img, 512, 256) };
  }));
}

function sponsorTexture(s, img, w, h) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d");
  g.fillStyle = s.bg || "#ffffff"; g.fillRect(0, 0, w, h);
  if (img) {
    const k = Math.min((w * 0.8) / img.width, (h * 0.8) / img.height);
    const iw = img.width * k, ih = img.height * k;
    g.drawImage(img, (w - iw) / 2, (h - ih) / 2, iw, ih);
  } else {
    g.fillStyle = s.fg || "#111111";
    g.font = `800 ${Math.floor(h * 0.55)}px "Segoe UI", system-ui, sans-serif`;
    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(s.name.toUpperCase(), w / 2, h / 2 + 2, w * 0.9);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

// ---------- helpers ----------
function trackFrames(cl) {
  // per-point position, and left normal in the x/z plane
  const n = cl.length, out = [];
  for (let i = 0; i < n; i++) {
    const a = cl[(i - 1 + n) % n], b = cl[(i + 1) % n];
    const tx = b[1] - a[1], tz = Z(b[3]) - Z(a[3]);
    const L = Math.hypot(tx, tz) || 1;
    out.push({ p: new THREE.Vector3(cl[i][1], cl[i][2], Z(cl[i][3])), nx: -tz / L, nz: tx / L });
  }
  return out;
}

const LIVERY = [0xe8e8e8, 0xd7263d, 0x1b998b, 0x2e86de, 0xf4a259, 0x8e44ad, 0x222222, 0xf1c40f];

export class View3D {
  constructor(el, state) {
    this.el = el; this.state = state;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    el.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x9fc3d6);
    this.scene.fog = new THREE.Fog(0x9fc3d6, 800, 3000);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.5, 8000);
    this.camera.position.set(0, 600, 900);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.camMode = "orbit";

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x445544, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(400, 800, 300);
    this.scene.add(sun);

    this.cars = new Map();       // car_id -> {mesh, yaw}
    this.drones = new Map();
    this.markers = new Map();
    this.sponsorsReady = loadSponsors().then((s) => (this.sponsors = s));
    this.resize();
  }

  resize() {
    const r = this.el.getBoundingClientRect();
    if (!r.width) return;
    this.renderer.setSize(r.width, r.height);
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
  }

  async setTrack(track) {
    await this.sponsorsReady;
    if (this.trackGroup) this.scene.remove(this.trackGroup);
    const g = (this.trackGroup = new THREE.Group());
    const cl = track.centerline, fr = trackFrames(cl), n = fr.length;

    // ground
    const ys = cl.map((p) => p[2]);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(10000, 10000), new THREE.MeshLambertMaterial({ color: 0x5f7f4f }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = Math.min(...ys) - 0.6;
    g.add(ground);

    // asphalt ribbon
    const pos = [], idx = [];
    for (const f of fr) {
      pos.push(f.p.x + f.nx * TRACK_HALF_WIDTH, f.p.y + 0.05, f.p.z + f.nz * TRACK_HALF_WIDTH);
      pos.push(f.p.x - f.nx * TRACK_HALF_WIDTH, f.p.y + 0.05, f.p.z - f.nz * TRACK_HALF_WIDTH);
    }
    for (let i = 0; i < n; i++) {
      const a = 2 * i, b = 2 * ((i + 1) % n);
      idx.push(a, b, a + 1, a + 1, b, b + 1);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geo.setIndex(idx); geo.computeVertexNormals();
    g.add(new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: 0x3b3f42, side: THREE.DoubleSide })));

    // start/finish line
    const start = new THREE.Mesh(new THREE.PlaneGeometry(1.5, TRACK_HALF_WIDTH * 2), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    start.rotation.x = -Math.PI / 2;
    start.rotation.z = Math.atan2(fr[0].nz, fr[0].nx);
    start.position.copy(fr[0].p).add(new THREE.Vector3(0, 0.08, 0));
    g.add(start);

    // sponsor barriers on both sides, one board every PANEL_LEN metres
    const step = Math.max(1, Math.round((PANEL_LEN / track.length_m) * n));
    const boardMats = this.sponsors.map((s) => new THREE.MeshBasicMaterial({ map: s.board, side: THREE.DoubleSide }));
    const postMat = new THREE.MeshLambertMaterial({ color: 0x9aa3a8 });
    let k = 0;
    for (let i = 0; i < n; i += step) {
      const a = fr[i], b = fr[(i + step) % n];
      for (const side of [1, -1]) {
        const pa = a.p.clone().add(new THREE.Vector3(a.nx * BARRIER_OFFSET * side, 0, a.nz * BARRIER_OFFSET * side));
        const pb = b.p.clone().add(new THREE.Vector3(b.nx * BARRIER_OFFSET * side, 0, b.nz * BARRIER_OFFSET * side));
        const len = pa.distanceTo(pb);
        if (len < 1) continue;
        const board = new THREE.Mesh(new THREE.PlaneGeometry(len, BARRIER_HEIGHT), boardMats[k % boardMats.length]);
        board.position.copy(pa).lerp(pb, 0.5).add(new THREE.Vector3(0, BARRIER_HEIGHT / 2 + 0.2, 0));
        board.rotation.y = -Math.atan2(pb.z - pa.z, pb.x - pa.x);
        g.add(board);
        const post = new THREE.Mesh(new THREE.BoxGeometry(0.15, BARRIER_HEIGHT + 0.3, 0.15), postMat);
        post.position.copy(pa).add(new THREE.Vector3(0, (BARRIER_HEIGHT + 0.3) / 2, 0));
        g.add(post);
        k++;
      }
    }
    this.scene.add(g);

    const c = new THREE.Box3().setFromObject(g.children[1]).getCenter(new THREE.Vector3());
    this.center = c;
    this.controls.target.copy(c);
    this.camera.position.set(c.x, c.y + 700, c.z + 900);
  }

  makeCar(id) {
    const grp = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(4.6, 1.1, 2.0), new THREE.MeshLambertMaterial({ color: LIVERY[id % LIVERY.length] }));
    body.position.y = 0.55;
    grp.add(body);
    const cabin = new THREE.Mesh(new THREE.BoxGeometry(2.0, 0.5, 1.6), new THREE.MeshLambertMaterial({ color: 0x1a1a1a }));
    cabin.position.set(-0.3, 1.35, 0);
    grp.add(cabin);
    if (this.sponsors?.length) {
      const s = this.sponsors[id % this.sponsors.length];
      const roof = new THREE.Mesh(new THREE.PlaneGeometry(2.0, 1.6), new THREE.MeshBasicMaterial({ map: s.roof }));
      roof.rotation.x = -Math.PI / 2;
      roof.rotation.z = -Math.PI / 2;       // text reads along the car
      roof.position.set(-0.3, 1.61, 0);
      grp.add(roof);
      const hood = roof.clone();
      hood.scale.set(0.8, 1.1, 1);
      hood.position.set(1.5, 1.11, 0);
      grp.add(hood);
    }
    grp.scale.setScalar(1.6);             // slightly exaggerated so cars read at track scale
    this.scene.add(grp);
    return { mesh: grp, yaw: 0 };
  }

  makeDrone(d) {
    const col = d.pilot === "fly" ? 0xf2b729 : 0x58a6ff;
    const grp = new THREE.Group();
    const mat = new THREE.MeshLambertMaterial({ color: col });
    grp.add(new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.3, 1.2), mat));
    for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const rotor = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, 0.05, 16), new THREE.MeshLambertMaterial({ color: 0x222222 }));
      rotor.position.set(x * 0.8, 0.2, z * 0.8);
      grp.add(rotor);
    }
    const cone = new THREE.Mesh(new THREE.ConeGeometry(4, 10, 24, 1, true),
      new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }));
    cone.position.y = -5;
    grp.add(cone);
    grp.scale.setScalar(DRONE_SCALE);
    this.scene.add(grp);
    return { mesh: grp, cone };
  }

  setCamera(mode) {
    this.camMode = mode;
    this.controls.enabled = mode === "orbit";
  }

  update(now) {
    const s = this.state;
    // cars: ease toward latest frame, face direction of travel
    for (const c of s.cars.values()) {
      let car = this.cars.get(c.car_id);
      if (!car) { if (!this.sponsors) continue; car = this.makeCar(c.car_id); this.cars.set(c.car_id, car); car.mesh.position.set(c.x, c.y, Z(c.z)); }
      const target = new THREE.Vector3(c.x, c.y, Z(c.z));
      const dx = target.x - car.mesh.position.x, dz = target.z - car.mesh.position.z;
      if (dx * dx + dz * dz > 0.04) car.yaw = Math.atan2(dz, dx);
      car.mesh.position.lerp(target, 0.35);
      car.mesh.rotation.y = -car.yaw;
      const flagged = [...s.events.values()].some((e) => e.car_ids.includes(c.car_id) && e.type === "incident");
      car.mesh.children[0].material.emissive?.setHex(flagged ? 0x661111 : 0x000000);
    }
    // drones
    for (const d of s.drones) {
      let dr = this.drones.get(d.drone_id);
      if (!dr) { dr = this.makeDrone(d); this.drones.set(d.drone_id, dr); dr.mesh.position.set(d.x, d.y, Z(d.z)); }
      dr.mesh.position.lerp(new THREE.Vector3(d.x, d.y, Z(d.z)), 0.35);
      dr.mesh.rotation.y = -Math.atan2(Z(d.vz), d.vx);
      dr.cone.material.opacity = d.mode === "patrol" ? 0.1 : 0.28;
    }
    // event markers
    const pulse = 1 + 0.25 * Math.sin(now / 200);
    for (const [id, m] of this.markers) if (!s.events.has(id)) { this.scene.remove(m); this.markers.delete(id); }
    for (const e of s.events.values()) {
      let m = this.markers.get(e.id);
      if (!m) {
        m = new THREE.Mesh(new THREE.RingGeometry(10, 13, 48),
          new THREE.MeshBasicMaterial({ color: e.type === "incident" ? 0xff5a4f : 0xf2b729, transparent: true, opacity: 0.85, side: THREE.DoubleSide }));
        m.rotation.x = -Math.PI / 2;
        this.scene.add(m); this.markers.set(e.id, m);
      }
      m.position.set(e.x, e.y + 0.3, Z(e.z));
      m.scale.setScalar(pulse);
      m.material.color.setHex(e.type === "incident" ? 0xff5a4f : 0xf2b729);
    }
    // cameras
    if (this.camMode === "fly") {
      const d = s.drones.find((x) => x.pilot === "fly"), dr = d && this.drones.get(d.drone_id);
      if (dr) {
        const back = new THREE.Vector3(-Math.cos(-dr.mesh.rotation.y), 0, -Math.sin(-dr.mesh.rotation.y)).multiplyScalar(30);
        this.camera.position.lerp(dr.mesh.position.clone().add(back).add(new THREE.Vector3(0, 12, 0)), 0.08);
        const look = d.target ? new THREE.Vector3(d.target[0], d.target[1] - 20, Z(d.target[2])) : dr.mesh.position;
        this.camera.lookAt(look);
      }
    } else if (this.camMode === "incident") {
      const e = [...s.events.values()].find((x) => x.type === "incident") || [...s.events.values()][0];
      if (e) {
        this.camera.position.lerp(new THREE.Vector3(e.x + 40, e.y + 35, Z(e.z) + 40), 0.05);
        this.camera.lookAt(e.x, e.y, Z(e.z));
      }
    } else {
      this.controls.update();
    }
    this.renderer.render(this.scene, this.camera);
  }
}
