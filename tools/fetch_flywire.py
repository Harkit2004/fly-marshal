"""Download the real FlyWire FAFB v783 data and build everything the fly brain needs.

Sources (public, no login):
  https://storage.googleapis.com/flywire-data/codex/data/fafb/783/
      neurons.csv.gz                  root_id, group, nt_type, ...                 1.7 MB
      consolidated_cell_types.csv.gz  root_id, primary_type, additional_type(s)    0.9 MB
      classification.csv.gz           root_id, flow, super_class, class, ..., side 0.9 MB
      coordinates.csv.gz              root_id, position "[x y z]" (nm), ...        5.3 MB
      fafb_v783_princeton_synapse_table.csv.gz   per-synapse table with size     2.7 GB  (--synapses)
  https://github.com/navis-org/navis-flybrains  flybrains/meshes/FLYWIRE.ply        1.0 MB  (GPL-3.0, not committed)

Builds:
  data/flywire/fly_neurons_real.csv    what third_party/flybrain's RealFlyBrain loads:
      root_id, primary_type, nt_type  (+ side, super_class, x, y, z for our viewer)
      Rows are ordered LEFT side first, then unknown/centre, then RIGHT. RealFlyBrain splits its
      photoreceptor and motion-neuron pools into halves by index; with this order those halves
      are the anatomical left and right, instead of the arbitrary split its README warns about.
  dashboard/assets/flybrain/brain.json real positions of a sampled set of simulated neurons,
      coloured by FlyWire super_class, plus the mesh transform
  dashboard/assets/flybrain/FLYWIRE.ply  the FlyWire brain surface mesh

The synapse table is read directly by drones/flybrain_real.py (no 3.7 GB CSV conversion).

Usage:
  python tools/fetch_flywire.py                 # small files + mesh (~10 MB), build viewer data
  python tools/fetch_flywire.py --synapses      # also the 2.7 GB synapse table (needed to run the connectome)
  set FLYBRAIN_DATA=data\\flywire  then  python brain.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import DATA, ROOT  # noqa: E402

BUCKET = "https://storage.googleapis.com/flywire-data/codex/data/fafb/783/"
MESH_URL = "https://raw.githubusercontent.com/navis-org/navis-flybrains/main/flybrains/meshes/FLYWIRE.ply"
SMALL = ["neurons.csv.gz", "consolidated_cell_types.csv.gz", "classification.csv.gz", "coordinates.csv.gz"]
SYNAPSES = "fafb_v783_princeton_synapse_table.csv.gz"
OUT = DATA / "flywire"
VIEWER = ROOT / "dashboard" / "assets" / "flybrain"
PHOTO_TYPES = {"R1-6", "R7", "R8"}

# viewer colour groups from FlyWire annotations (photoreceptors split out of "optic" by cell type)
GROUPS = ["photoreceptor", "optic", "visual_projection", "central", "sensory", "descending",
          "ascending", "motor", "endocrine", "other"]


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have {dest.name}")
        return
    print(f"  downloading {url.rsplit('/', 1)[-1]} ...", flush=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    tmp.replace(dest)


def build_neurons() -> pd.DataFrame:
    neurons = pd.read_csv(OUT / "neurons.csv.gz", usecols=["root_id", "nt_type"])
    types = pd.read_csv(OUT / "consolidated_cell_types.csv.gz", usecols=["root_id", "primary_type"])
    cls = pd.read_csv(OUT / "classification.csv.gz", usecols=["root_id", "super_class", "side"])
    coords = pd.read_csv(OUT / "coordinates.csv.gz", usecols=["root_id", "position"]).drop_duplicates("root_id")
    xyz = coords.position.str.strip("[]").str.split(expand=True).astype(float)
    coords = pd.DataFrame({"root_id": coords.root_id, "x": xyz[0], "y": xyz[1], "z": xyz[2]})

    df = (neurons.merge(types, on="root_id", how="left")
                 .merge(cls, on="root_id", how="left")
                 .merge(coords, on="root_id", how="left"))
    side_rank = df.side.map({"left": 0, "right": 2}).fillna(1)
    df = df.assign(_s=side_rank).sort_values(["_s", "root_id"], kind="stable").drop(columns="_s").reset_index(drop=True)
    df.to_csv(OUT / "fly_neurons_real.csv", index=False)

    photo = df.primary_type.isin(PHOTO_TYPES)
    for name, m in [("R1-6", df.primary_type == "R1-6"), ("photoreceptors", photo),
                    ("descending", df.super_class == "descending")]:
        sides = df[m].side.value_counts().to_dict()
        print(f"  {name:15s} {int(m.sum()):6d}   {sides}")
    print(f"  neurons: {len(df):,}   with coordinates: {df.x.notna().sum():,}")
    return df


def group_of(row) -> str:
    if row.primary_type in PHOTO_TYPES:
        return "photoreceptor"
    sc = row.super_class if isinstance(row.super_class, str) else ""
    return sc if sc in GROUPS else ("other" if sc else "other")


def build_viewer(df: pd.DataFrame, n_sample: int, seed: int) -> None:
    VIEWER.mkdir(parents=True, exist_ok=True)
    have = df[df.x.notna()].copy()
    have["group"] = have.apply(group_of, axis=1)
    rng = np.random.default_rng(seed)
    # keep every descending neuron (they drive the motor readout), stratify the rest by group
    keep = [have[have.group == "descending"]]
    rest = have[have.group != "descending"]
    quota = max(0, n_sample - len(keep[0]))
    frac = quota / max(1, len(rest))
    for g, part in rest.groupby("group"):
        k = min(len(part), max(50, int(round(len(part) * frac))))
        keep.append(part.sample(k, random_state=int(rng.integers(1 << 31))))
    s = pd.concat(keep).sort_index()

    center = have[["x", "y", "z"]].mean().to_numpy()
    scale = 1e-3                           # nm -> µm
    pos = (s[["x", "y", "z"]].to_numpy() - center) * scale
    brain = {
        "source": "FlyWire FAFB v783 (Codex): coordinates.csv, classification.csv, consolidated_cell_types.csv",
        "units": "micrometres, centred on the mean neuron position; FlyWire axes (x right, y down, z posterior)",
        "center_nm": center.round(1).tolist(), "scale": scale,
        "groups": GROUPS,
        "root_id": [str(r) for r in s.root_id],
        "index": s.index.astype(int).tolist(),       # row in fly_neurons_real.csv = RealFlyBrain neuron index
        "group": [GROUPS.index(g) for g in s.group],
        "pos": np.round(pos, 2).flatten().tolist(),
        "mesh": "FLYWIRE.ply" if (VIEWER / "FLYWIRE.ply").exists() else None,
    }
    (VIEWER / "brain.json").write_text(json.dumps(brain, separators=(",", ":")))
    counts = s.group.value_counts().to_dict()
    print(f"  viewer sample: {len(s):,} real neurons  {counts}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synapses", action="store_true", help="also download the 2.7 GB synapse table")
    ap.add_argument("--sample", type=int, default=20000, help="neurons shown in the 3D viewer")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    VIEWER.mkdir(parents=True, exist_ok=True)

    print("FlyWire files:")
    for f in SMALL:
        fetch(BUCKET + f, OUT / f)
    fetch(MESH_URL, VIEWER / "FLYWIRE.ply")
    if args.synapses:
        fetch(BUCKET + SYNAPSES, OUT / SYNAPSES)

    print("building fly_neurons_real.csv:")
    df = build_neurons()
    print("building viewer data:")
    build_viewer(df, args.sample, args.seed)
    if not (OUT / SYNAPSES).exists():
        print(f"\nViewer ready. To run the real connectome: python tools/fetch_flywire.py --synapses")
    print(f"Then: set FLYBRAIN_DATA={OUT}")


if __name__ == "__main__":
    main()
