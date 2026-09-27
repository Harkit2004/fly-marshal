import * as THREE from 'three';

// Geometry and base materials stay shared. Only skin-specific materials are cloned.
export class CarSkins {
  static async load(url) {
    try {
      const response = await fetch(url, {cache: 'no-cache'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return new CarSkins(await response.json(), new URL(url, document.baseURI));
    } catch (error) {
      console.warn('Car liveries unavailable; using base textures.', error);
      return null;
    }
  }
  constructor(manifest, url) {
    this.manifest = manifest;
    this.url = url;
    this.textures = new Map();
    this.materials = new Map();
    this.disposed = false;
  }
  texture(file) {
    if (!this.textures.has(file)) {
      this.textures.set(file, new THREE.TextureLoader().loadAsync(new URL(file, this.url).href).then(texture => {
        texture.flipY = false;
        texture.colorSpace = THREE.SRGBColorSpace;
        if (this.disposed) texture.dispose();
        return texture;
      }));
    }
    return this.textures.get(file);
  }
  async material(base, file) {
    const key = `${base.uuid}:${file}`;
    if (!this.materials.has(key)) {
      this.materials.set(key, this.texture(file).then(texture => {
        const mat = base.clone();
        mat.map = texture;
        mat.needsUpdate = true;
        if (this.disposed) mat.dispose();
        return mat;
      }));
    }
    return this.materials.get(key);
  }
  async apply(car, model, skin) {
    const key = JSON.stringify([model, skin]);
    if (car.skinKey === key) return;
    car.skinKey = key;
    // Immediately undo previous overrides if the same car slot changes skin.
    for (const part of car.skinParts) part.mesh.material = part.base;
    car.skinStatus = 'base';
    const maps = model === this.manifest.car_model && this.manifest.skins[skin];
    if (!maps) return;
    try {
      const updates = await Promise.all(car.skinParts.map(async part => {
        const bases = Array.isArray(part.base) ? part.base : [part.base];
        const materials = await Promise.all(bases.map(base => maps[base.name]
          ? this.material(base, maps[base.name]) : base));
        return [part.mesh, Array.isArray(part.base) ? materials : materials[0]];
      }));
      if (car.skinKey !== key || this.disposed) return;
      for (const [mesh, material] of updates) mesh.material = material;
      car.skinStatus = skin;
    } catch (error) {
      console.warn(`Skin ${skin} unavailable; retaining base textures.`, error);
    }
  }
  dispose() {
    this.disposed = true;
    for (const p of this.materials.values()) p.then(m => m.dispose()).catch(() => {});
    for (const p of this.textures.values()) p.then(t => t.dispose()).catch(() => {});
  }
}
