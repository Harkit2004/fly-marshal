// 3D fly-brain viewer built from real FlyWire FAFB v783 data (tools/fetch_flywire.py):
//   assets/flybrain/brain.json   sampled simulated neurons at their real FlyWire coordinates,
//                                grouped by FlyWire super_class (photoreceptors split out by cell type)
//   assets/flybrain/FLYWIRE.ply  FlyWire brain surface mesh (navis-flybrains, GPL-3.0; downloaded, not committed)
// brain.py sends, per control step, which of those neurons spiked in the real connectome
// simulation. With the placeholder controller nothing is sent and the brain stays dark.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { PLYLoader } from "three/addons/loaders/PLYLoader.js";

const BASE = "assets/flybrain/";
export const GROUP_INFO = {
  photoreceptor: { label: "Photoreceptors", color: 0x4cc3ff },
  optic: { label: "Optic lobe", color: 0x8b7bff },
  visual_projection: { label: "Visual projection", color: 0xc77dff },
  central: { label: "Central brain", color: 0x3ccf91 },
  sensory: { label: "Sensory", color: 0x5ad1c8 },
  descending: { label: "Descending (motor cmd)", color: 0xf2b729 },
  ascending: { label: "Ascending", color: 0xff8f5a },
  motor: { label: "Motor", color: 0xff5a4f },
  endocrine: { label: "Endocrine", color: 0xe06c9f },
  other: { label: "Other", color: 0x9aa3a8 },
};

export class FlyBrain3D {
  constructor(canvas, onStatus) {
    this.canvas = canvas;
    this.onStatus = onStatus || (() => {});
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 1, 10000);
    this.camera.position.set(0, 0, 1500);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 0.6;
    this.groups = [];
    this.ready = this.load();
    this.resize();
  }

  // FlyWire axes: x = right, y = down (ventral), z = posterior. Show it upright, front facing the viewer.
  static toView(x, y, z) { return [x, -y, -z]; }

  async load() {
    let brain;
    try {
      const r = await fetch(BASE + "brain.json");
      if (!r.ok) throw new Error(r.status);
      brain = await r.json();
    } catch {
      this.onStatus("missing");
      return false;
    }
    this.brain = brain;
    this.groups = brain.groups;
    const n = brain.root_id.length;
    const pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
    this.base = new Float32Array(n * 3);
    this.glow = new Float32Array(n);
    this.groupOf = new Uint8Array(brain.group);
    this.groupColor = this.groups.map((g) => new THREE.Color((GROUP_INFO[g] || GROUP_INFO.other).color));
    this.groupCount = this.groups.map((_, k) => brain.group.filter((g) => g === k).length);
    for (let i = 0; i < n; i++) {
      pos.set(FlyBrain3D.toView(brain.pos[3 * i], brain.pos[3 * i + 1], brain.pos[3 * i + 2]), 3 * i);
      const c = this.groupColor[brain.group[i]];
      this.base.set([c.r * 0.3, c.g * 0.3, c.b * 0.3], 3 * i);
    }
    col.set(this.base);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    this.colors = geo.attributes.color;
    const dot = (() => {
      const cv = document.createElement("canvas"); cv.width = cv.height = 64;
      const g = cv.getContext("2d"), grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      grd.addColorStop(0, "rgba(255,255,255,1)"); grd.addColorStop(0.4, "rgba(255,255,255,.55)"); grd.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = grd; g.fillRect(0, 0, 64, 64);
      return new THREE.CanvasTexture(cv);
    })();
    this.points = new THREE.Points(geo, new THREE.PointsMaterial({
      size: 7, vertexColors: true, map: dot, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    this.scene.add(this.points);

    geo.computeBoundingSphere();
    const r = geo.boundingSphere.radius;
    this.camera.position.set(0, r * 0.15, r * 2.6);
    this.camera.far = r * 20; this.camera.updateProjectionMatrix();

    if (brain.mesh) {
      try {
        const g = await new PLYLoader().loadAsync(BASE + brain.mesh);
        const p = g.attributes.position, [cx, cy, cz] = brain.center_nm, s = brain.scale;
        for (let i = 0; i < p.count; i++) {
          const [x, y, z] = FlyBrain3D.toView((p.getX(i) - cx) * s, (p.getY(i) - cy) * s, (p.getZ(i) - cz) * s);
          p.setXYZ(i, x, y, z);
        }
        p.needsUpdate = true;
        g.computeVertexNormals();
        const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0x9fd8ff, transparent: true, opacity: 0.05, depthWrite: false, side: THREE.DoubleSide }));
        const wire = new THREE.LineSegments(new THREE.EdgesGeometry(g, 25), new THREE.LineBasicMaterial({ color: 0x9fd8ff, transparent: true, opacity: 0.12 }));
        this.scene.add(mesh, wire);
      } catch (e) { console.warn("brain mesh failed", e); }
    }
    this.onStatus("ready");
    return true;
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width) return;
    this.renderer.setSize(r.width, r.height, false);
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
  }

  fire(indices) {
    if (!this.glow) return;
    for (const j of indices || []) if (j >= 0 && j < this.glow.length) this.glow[j] = 1;
  }

  update() {
    if (this.colors) {
      const a = this.colors.array;
      let dirty = false;
      for (let j = 0; j < this.glow.length; j++) {
        const g = this.glow[j];
        if (g === 0) continue;
        const c = this.groupColor[this.groupOf[j]];
        a[3 * j] = this.base[3 * j] + (c.r + 0.5) * g;
        a[3 * j + 1] = this.base[3 * j + 1] + (c.g + 0.5) * g;
        a[3 * j + 2] = this.base[3 * j + 2] + (c.b + 0.5) * g;
        this.glow[j] = g < 0.02 ? 0 : g * 0.85;
        if (this.glow[j] === 0) { a[3 * j] = this.base[3 * j]; a[3 * j + 1] = this.base[3 * j + 1]; a[3 * j + 2] = this.base[3 * j + 2]; }
        dirty = true;
      }
      if (dirty) this.colors.needsUpdate = true;
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
