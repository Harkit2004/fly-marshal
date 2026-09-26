"""Load the real FlyWire connectome controller from third_party/flybrain.

RealFlyBrain lives inside flybrain_tello_real_brain.py, a Tello script that imports
djitellopy / pynput / TkAgg at module level. Instead of copying it, we extract just
the class with `ast` and build the connectome the same way that script does, so
upstream fixes to the neuron model come along automatically.

Data (not in the flybrain repo, see its README "Required Data Files"):
  $FLYBRAIN_DATA/fly_neurons_real.csv     columns: root_id, primary_type, nt_type
  $FLYBRAIN_DATA/fly_synapses_real.csv    columns: pre_root_id, post_root_id, size  (~85M rows, ~3.7 GB)
Set FLYBRAIN_SYNAPSE_STRIDE=N to keep every Nth synapse if 8 GB VRAM is tight.

Interface used by drones/pilots.py:
  brain = RealFlyBrainAdapter.load()          # or None when data/torch is missing
  brain.step([forward, left, right, vertical]) -> {forward, turn, climb, spikes}
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import numpy as np

from shared.config import FLY_REGIONS, FLY_SAMPLE_PER_REGION, ROOT

FLYBRAIN_DIR = ROOT / "third_party" / "flybrain"
SOURCE = FLYBRAIN_DIR / "flybrain_tello_real_brain.py"


def _extract_class(name: str = "RealFlyBrain"):
    import torch
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    ns = {"np": np, "torch": torch}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns[name]


def build_connectome(data_dir: Path, stride: int, device):
    """Same loading steps as flybrain_tello_real_brain.py (neuron typing, Dale's law)."""
    import pandas as pd
    import torch

    neurons = pd.read_csv(data_dir / "fly_neurons_real.csv")
    n = len(neurons)
    pr = set(neurons[neurons["primary_type"].isin(["R1-6", "R7", "R8"])].index)
    motion = set(neurons[neurons["primary_type"].str.contains(r"^(?:T4|T5|Tm|Mi|Lo)", na=False, regex=True)].index)
    dn = set(neurons[neurons["primary_type"].str.startswith("DN", na=False)].index)

    rid = {r: i for i, r in enumerate(neurons["root_id"].values)}
    inhib = neurons["nt_type"].isin(["GABA"]).values
    pre_l, post_l, size_l = [], [], []
    for chunk in pd.read_csv(data_dir / "fly_synapses_real.csv", usecols=["pre_root_id", "post_root_id", "size"],
                             chunksize=4_000_000, low_memory=False):
        if stride > 1:
            chunk = chunk.iloc[::stride]
        pre = chunk["pre_root_id"].map(rid).fillna(-1).to_numpy(dtype=np.int64)
        post = chunk["post_root_id"].map(rid).fillna(-1).to_numpy(dtype=np.int64)
        ok = (pre >= 0) & (post >= 0)
        pre_l.append(pre[ok].astype(np.int32)); post_l.append(post[ok].astype(np.int32))
        size_l.append(chunk["size"].to_numpy(dtype=np.float32)[ok])
    pre, post, sizes = np.concatenate(pre_l), np.concatenate(post_l), np.concatenate(size_l)
    w = np.clip(sizes / (sizes.max() + 1e-6), 0.1, 2.0).astype(np.float32)
    w *= np.where(inhib[pre], -1.0, 1.0).astype(np.float32)
    idx = torch.from_numpy(np.stack([pre, post])).long().to(device)
    conn = torch.sparse_coo_tensor(idx, torch.from_numpy(w).to(device), (n, n), device=device).coalesce()
    return conn, neurons, pr, dn, motion, n


class RealFlyBrainAdapter:
    """Maps RealFlyBrain's Tello RC output (-100..100) onto our forward/turn/climb (0..1 / -1..1),
    and reports which sampled neurons fired (for the dashboard's 3D brain view)."""

    def __init__(self, brain, seed: int = 0):
        import torch
        self.brain = brain
        b = brain
        motion = b.motion_indices
        half = len(motion) // 2
        special = set(b.r16_idx.tolist()) | set(b.r78_idx.tolist()) | set(motion.tolist()) | set(b.dn_indices.tolist())
        rng = np.random.default_rng(seed)
        central = np.array(sorted(set(rng.choice(b.n_neurons, min(b.n_neurons, 20000), replace=False)) - special))
        groups = {
            "photo_l": b.r16_left_idx.cpu().numpy(), "photo_r": b.r16_right_idx.cpu().numpy(),
            "motion_l": motion[:half].cpu().numpy(), "motion_r": motion[half:].cpu().numpy(),
            "central": central, "descending": b.dn_indices.cpu().numpy(),
        }
        # fixed sample per region, same order as shared.config.FLY_REGIONS
        sample = []
        for r in FLY_REGIONS:
            g = groups[r]
            k = min(FLY_SAMPLE_PER_REGION, len(g))
            pick = rng.choice(g, k, replace=False) if k else np.zeros(0, int)
            sample.append(np.pad(pick, (0, FLY_SAMPLE_PER_REGION - k), constant_values=-1))
        self.sample = torch.as_tensor(np.concatenate(sample), device=b.device)
        self.valid = self.sample >= 0
        self.fired_acc = torch.zeros(b.n_neurons, dtype=torch.bool, device=b.device)
        # record every spike in the control window, not just the last 1 ms substep
        orig_step = b._step

        def step(i_input):
            orig_step(i_input)
            self.fired_acc |= b.spikes
        b._step = step

    @classmethod
    def load(cls, data_dir: str | None = None, substeps: int = 20):
        data_dir = Path(data_dir or os.environ.get("FLYBRAIN_DATA", FLYBRAIN_DIR))
        if not (data_dir / "fly_neurons_real.csv").exists():
            print(f"[flybrain] no connectome data in {data_dir}; using the placeholder fly")
            return None
        try:
            import torch
        except ImportError:
            print("[flybrain] torch not installed; using the placeholder fly")
            return None
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        stride = int(os.environ.get("FLYBRAIN_SYNAPSE_STRIDE", "1"))
        print(f"[flybrain] loading connectome from {data_dir} on {device} (stride {stride}) ...")
        RealFlyBrain = _extract_class()
        conn, neurons, pr, dn, motion, n = build_connectome(data_dir, stride, device)
        print(f"[flybrain] {n:,} neurons, {conn._nnz():,} synapses")
        return cls(RealFlyBrain(conn, neurons, pr, dn, motion, n, device, substeps=substeps))

    def step(self, flow: list[float]) -> dict:
        self.fired_acc.zero_()
        motor, metrics = self.brain.compute(np.clip(np.asarray(flow, dtype=np.float32), 0, 1))
        hit = self.fired_acc[self.sample.clamp(min=0)] & self.valid
        fired = hit.nonzero().flatten().tolist()
        per = hit.view(len(FLY_REGIONS), FLY_SAMPLE_PER_REGION).float().mean(dim=1).tolist()
        return {
            "forward": max(0.0, motor["forward"] / 100.0),
            "turn": motor["yaw"] / 100.0,
            "climb": motor["vertical"] / 100.0,
            "spikes": int(metrics["total_spikes"]),
            "regions": {r: round(v, 3) for r, v in zip(FLY_REGIONS, per)},
            "fired": fired,
            "source": "flywire",
        }
