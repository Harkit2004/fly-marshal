// 3D fly-brain viewer. brain.py sends, per tick, which of a fixed sample of neurons
// fired (FLY_SAMPLE_PER_REGION per region, regions in FLY_REGIONS order). Each sampled
// neuron is a point here; it flashes when it fires and fades out.
//
// Positions are a schematic fly-brain layout (two optic lobes, central brain, descending
// neurons running down the neck), not real FlyWire coordinates. Real per-neuron positions
// could replace place() if the FlyWire coordinate table is added.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export const REGIONS = ["photo_l", "photo_r", "motion_l", "motion_r", "central", "descending"];
export const PER_REGION = 250;
export const REGION_INFO = {
  photo_l: { label: "Photoreceptors L", color: 0x4cc3ff },
  photo_r: { label: "Photoreceptors R", color: 0x4cc3ff },
  motion_l: { label: "Motion (T4/T5) L", color: 0x8b7bff },
  motion_r: { label: "Motion (T4/T5) R", color: 0x8b7bff },
  central: { label: "Central brain", color: 0x3ccf91 },
  descending: { label: "Descending (motor)", color: 0xf2b729 },
};

function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

function place(region, r) {
  const g = () => r() * 2 - 1;
  const onShell = (rx, ry, rz) => {             // random point on an ellipsoid surface
    let x, y, z, n;
    do { x = g(); y = g(); z = g(); n = Math.hypot(x, y, z); } while (n < 0.2 || n > 1);
    return [x / n * rx, y / n * ry, z / n * rz];
  };
  const inside = (rx, ry, rz) => {
    let x, y, z;
    do { x = g(); y = g(); z = g(); } while (x * x + y * y + z * z > 1);
    return [x * rx, y * ry, z * rz];
  };
  switch (region) {
    case "photo_l": { const [x, y, z] = onShell(0.55, 0.9, 0.75); return [x - 1.75, y, z]; }
    case "photo_r": { const [x, y, z] = onShell(0.55, 0.9, 0.75); return [x + 1.75, y, z]; }
    case "motion_l": { const [x, y, z] = inside(0.4, 0.7, 0.55); return [x - 1.6, y, z]; }
    case "motion_r": { const [x, y, z] = inside(0.4, 0.7, 0.55); return [x + 1.6, y, z]; }
    case "central": { const [x, y, z] = inside(1.0, 0.75, 0.6); return [x, y + 0.1, z]; }
    case "descending": { const t = r(); return [g() * 0.18, -0.6 - t * 1.6, g() * 0.18 - t * 0.3]; }
  }
  return [0, 0, 0];
}

export class FlyBrain3D {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
    this.camera.position.set(0, 0.6, 6.2);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 0.8;

    const n = REGIONS.length * PER_REGION;
    const pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
    this.base = new Float32Array(n * 3);
    this.glow = new Float32Array(n);
    const r = rng(7);
    const c = new THREE.Color();
    REGIONS.forEach((reg, k) => {
      c.setHex(REGION_INFO[reg].color);
      for (let i = 0; i < PER_REGION; i++) {
        const j = k * PER_REGION + i;
        pos.set(place(reg, r), j * 3);
        this.base.set([c.r * 0.22, c.g * 0.22, c.b * 0.22], j * 3);
      }
    });
    col.set(this.base);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    this.colors = geo.attributes.color;
    this.regionColor = REGIONS.map((reg) => new THREE.Color(REGION_INFO[reg].color));
    const sprite = (() => {
      const cv = document.createElement("canvas"); cv.width = cv.height = 64;
      const g = cv.getContext("2d"), grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      grd.addColorStop(0, "rgba(255,255,255,1)"); grd.addColorStop(0.35, "rgba(255,255,255,0.6)"); grd.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = grd; g.fillRect(0, 0, 64, 64);
      return new THREE.CanvasTexture(cv);
    })();
    this.points = new THREE.Points(geo, new THREE.PointsMaterial({
      size: 0.13, vertexColors: true, map: sprite, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    this.scene.add(this.points);

    // faint outline shells so the regions read as a brain
    const shell = (rx, ry, rz, x, y, color) => {
      const m = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 16),
        new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.06 }));
      m.scale.set(rx, ry, rz); m.position.set(x, y, 0); this.scene.add(m);
    };
    shell(0.6, 0.95, 0.8, -1.75, 0, 0x4cc3ff);
    shell(0.6, 0.95, 0.8, 1.75, 0, 0x4cc3ff);
    shell(1.05, 0.8, 0.65, 0, 0.1, 0x3ccf91);
    this.resize();
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width) return;
    this.renderer.setSize(r.width, r.height, false);
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
  }

  fire(indices) {
    for (const j of indices || []) if (j >= 0 && j < this.glow.length) this.glow[j] = 1;
  }

  update() {
    const a = this.colors.array;
    for (let j = 0; j < this.glow.length; j++) {
      const g = this.glow[j];
      if (g < 0.01 && this.glow[j] === 0) continue;
      const rc = this.regionColor[(j / PER_REGION) | 0];
      a[j * 3] = this.base[j * 3] + (rc.r * 1.4 + 0.3) * g;
      a[j * 3 + 1] = this.base[j * 3 + 1] + (rc.g * 1.4 + 0.3) * g;
      a[j * 3 + 2] = this.base[j * 3 + 2] + (rc.b * 1.4 + 0.3) * g;
      this.glow[j] = g < 0.01 ? 0 : g * 0.86;
    }
    this.colors.needsUpdate = true;
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
