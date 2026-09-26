"""Load the real FlyWire connectome controller from third_party/flybrain.

RealFlyBrain lives inside flybrain_tello_real_brain.py, a Tello script that imports
djitellopy / pynput / TkAgg at module level. Instead of copying it, we extract just
the class with `ast` and build the connectome the same way that script does, so
upstream fixes to the neuron model come along automatically.

Data: run `python tools/fetch_flywire.py --synapses` (public FlyWire v783 files), then
set FLYBRAIN_DATA=data/flywire. That folder holds:
  fly_neurons_real.csv                          root_id, primary_type, nt_type (+ side, super_class, x, y, z)
  fafb_v783_princeton_synapse_table.csv.gz      per-synapse table, read directly
  (or fly_synapses_real.csv                     pre_root_id, post_root_id, size, if you have the flybrain format)
Set FLYBRAIN_SYNAPSE_STRIDE=N to keep every Nth synapse (default 1 with CUDA, 8 on CPU).

Interface used by drones/pilots.py:
  brain = RealFlyBrainAdapter.load()          # None when data/torch is missing
  brain.step([forward, left, right, vertical]) -> {forward, turn, climb, spikes, groups, fired, source}
`fired` lists positions in dashboard/assets/flybrain/brain.json (real FlyWire neurons at their real
coordinates) whose simulated neuron spiked during this control step. Nothing is synthesised.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import numpy as np

from shared.config import DATA, ROOT
from shared.settings import get

FLYBRAIN_DIR = ROOT / "third_party" / "flybrain"
SOURCE = FLYBRAIN_DIR / "flybrain_tello_real_brain.py"
VIEWER_BRAIN = ROOT / "dashboard" / "assets" / "flybrain" / "brain.json"
PRINCETON = "fafb_v783_princeton_synapse_table.csv.gz"
ROOT_PREFIX = 720575940 * 10**9          # the princeton table stores root ids without this prefix


def _extract_class(name: str = "RealFlyBrain"):
    import torch
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    ns = {"np": np, "torch": torch}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns[name]


def _synapse_chunks(data_dir: Path):
    """Yield (pre_root_id, post_root_id, size) chunks from whichever synapse file is present."""
    import pandas as pd
    flat = data_dir / "fly_synapses_real.csv"
    if flat.exists():
        for c in pd.read_csv(flat, usecols=["pre_root_id", "post_root_id", "size"], chunksize=4_000_000, low_memory=False):
            yield c["pre_root_id"].to_numpy(np.int64), c["post_root_id"].to_numpy(np.int64), c["size"].to_numpy(np.float32)
        return
    cols = ["size", "pre_root_id_720575940", "post_root_id_720575940"]
    for c in pd.read_csv(data_dir / PRINCETON, usecols=cols, chunksize=4_000_000, low_memory=False):
        yield (c[cols[1]].to_numpy(np.int64) + ROOT_PREFIX, c[cols[2]].to_numpy(np.int64) + ROOT_PREFIX,
               c["size"].to_numpy(np.float32))


def build_connectome(data_dir: Path, stride: int, device):
    """Same steps as flybrain_tello_real_brain.py: exact cell-type pools, Dale's law signs."""
    import pandas as pd
    import torch

    neurons = pd.read_csv(data_dir / "fly_neurons_real.csv")
    n = len(neurons)
    pr = set(neurons[neurons["primary_type"].isin(["R1-6", "R7", "R8"])].index)
    motion = set(neurons[neurons["primary_type"].str.contains(r"^(?:T4|T5|Tm|Mi|Lo)", na=False, regex=True)].index)
    dn = set(neurons[neurons["primary_type"].str.startswith("DN", na=False)].index)

    ids = neurons["root_id"].to_numpy(np.int64)
    order = np.argsort(ids)
    sorted_ids = ids[order]
    inhib = neurons["nt_type"].isin(["GABA"]).to_numpy()

    def to_idx(r):
        pos = np.clip(np.searchsorted(sorted_ids, r), 0, n - 1)
        return np.where(sorted_ids[pos] == r, order[pos], -1)

    # Parsing the 2.7 GB table takes minutes, so the mapped edges are cached per stride.
    cache = data_dir / f"edges_stride{stride}.npz"
    if cache.exists() and cache.stat().st_mtime > (data_dir / "fly_neurons_real.csv").stat().st_mtime:
        z = np.load(cache)
        pre, post, sizes = z["pre"], z["post"], z["size"]
        print(f"[flybrain] edges from cache {cache.name}")
    else:
        pre_l, post_l, size_l = [], [], []
        rows = 0
        for pre_r, post_r, size in _synapse_chunks(data_dir):
            rows += len(size)
            if stride > 1:
                pre_r, post_r, size = pre_r[::stride], post_r[::stride], size[::stride]
            pre, post = to_idx(pre_r), to_idx(post_r)
            ok = (pre >= 0) & (post >= 0)
            pre_l.append(pre[ok].astype(np.int32)); post_l.append(post[ok].astype(np.int32)); size_l.append(size[ok])
            print(f"[flybrain]   {rows / 1e6:6.1f}M synapse rows read", end=chr(13), flush=True)
        print()
        pre, post, sizes = np.concatenate(pre_l), np.concatenate(post_l), np.concatenate(size_l)
        np.savez(cache, pre=pre, post=post, size=sizes)
        print(f"[flybrain] cached {len(pre):,} edges -> {cache.name}")
    w = np.clip(sizes / (sizes.max() + 1e-6), 0.1, 2.0).astype(np.float32)
    w *= np.where(inhib[pre], -1.0, 1.0).astype(np.float32)
    idx = torch.from_numpy(np.stack([pre, post])).long().to(device)
    conn = torch.sparse_coo_tensor(idx, torch.from_numpy(w).to(device), (n, n), device=device).coalesce()
    return conn, neurons, pr, dn, motion, n


class CorrectedLIF:
    """Leaky integrate-and-fire dynamics with the published Shiu et al. (2024) time constants,
    run on RealFlyBrain's own connectome tensor and neuron pools.

    Why: upstream RealFlyBrain uses tau_m = R_m*C_m = 10 * 2e-6 = 20 us with a 1 ms Euler step,
    so any input is amplified ~49x per step and the whole brain saturates identically for every
    input (see README findings). Here tau_m = 20 ms, tau_syn = 5 ms, and synaptic input is a
    voltage-like conductance g that each presynaptic spike bumps by W_SYN_MV * weight.

    Inputs follow upstream: forward -> all R1-6, left -> left R1-6, right -> right R1-6,
    vertical -> R7/R8 (left/right are anatomical because fly_neurons_real.csv is ordered by side).
    Readout: turn = left/right imbalance of simulated R1-6 firing. Forward and climb come from
    descending-neuron firing when it is active; otherwise they are marked as assisted.
    """

    TAU_M, TAU_SYN, DT = 0.020, 0.005, 1e-3
    W_SYN_MV = 0.275            # mV per unit synaptic weight (Shiu et al. use 0.275 mV per synapse)
    DRIVE_MV = 20.0             # photoreceptor drive at full optic-flow input

    def __init__(self, upstream, substeps: int = 20):
        import math
        import torch
        self.u = upstream
        self.device, self.n_neurons = upstream.device, upstream.n_neurons
        self.substeps = substeps
        self.decay = math.exp(-self.DT / self.TAU_SYN)
        self.torch = torch
        self.spikes = torch.zeros(self.n_neurons, dtype=torch.bool, device=self.device)
        self.reset_state()

    def reset_state(self):
        t = self.torch
        u = self.u
        self.v = t.full((self.n_neurons,), u.V_rest, device=self.device)
        self.g = t.zeros(self.n_neurons, device=self.device)
        self.ref = t.zeros(self.n_neurons, device=self.device)
        self.spikes = t.zeros(self.n_neurons, dtype=t.bool, device=self.device)

    def _step(self, drive):
        u = self.u
        self.ref = (self.ref - self.DT).clamp(min=0)
        syn = self.torch.sparse.mm(u.conn_T, self.spikes.float().unsqueeze(1)).squeeze()
        self.g = self.g * self.decay + syn * (self.W_SYN_MV * 1e-3)
        self.v = self.v + (u.V_rest - self.v + self.g + drive) * (self.DT / self.TAU_M)
        self.spikes = (self.v > u.V_thresh) & (self.ref <= 0)
        self.v[self.spikes] = u.V_rest
        self.ref[self.spikes] = u.tau_ref

    def compute(self, optic_flow):
        t, u = self.torch, self.u
        f = t.as_tensor(optic_flow, dtype=t.float32, device=self.device)
        mv = self.DRIVE_MV * 1e-3
        drive = t.zeros(self.n_neurons, device=self.device)
        drive[u.r16_idx] += f[0] * mv
        drive[u.r16_left_idx] += f[1] * mv
        drive[u.r16_right_idx] += f[2] * mv
        drive[u.r78_idx] += f[3] * mv
        count = t.zeros(self.n_neurons, device=self.device)
        for _ in range(self.substeps):
            self._step(drive)
            count += self.spikes.float()
        rate = count / (self.substeps * self.DT)                     # Hz
        rl, rr = rate[u.r16_left_idx].mean().item(), rate[u.r16_right_idx].mean().item()
        dn = rate[u.dn_indices].mean().item() if len(u.dn_indices) else 0.0
        motor = {"turn": (rl - rr) / (rl + rr + 1e-6)}
        if dn > 1.0:                                                 # descending neurons active
            motor["forward"] = min(1.0, dn / 100.0)
            motor["climb"] = max(-1.0, min(1.0, dn / 100.0 - 0.5))
        metrics = {"total_spikes": count.sum().item() / self.substeps, "dn_hz": dn,
                   "photo_l_hz": rl, "photo_r_hz": rr}
        return motor, metrics


class RealFlyBrainAdapter:
    """Maps RealFlyBrain's Tello RC output (-100..100) onto our forward/turn/climb, and reports which
    of the viewer's real FlyWire neurons spiked during the step."""

    def __init__(self, brain, neurons=None):
        import torch
        self.brain = brain
        b = brain            # RealFlyBrain or CorrectedLIF; both expose _step, spikes, compute
        self.fired_acc = torch.zeros(b.n_neurons, dtype=torch.bool, device=b.device)
        orig_step = b._step

        def step(i_input):            # record every spike in the control window, not only the last 1 ms
            orig_step(i_input)
            self.fired_acc |= b.spikes
        b._step = step

        self.sample = None
        if VIEWER_BRAIN.exists() and neurons is not None:
            vb = json.loads(VIEWER_BRAIN.read_text())
            by_id = {r: i for i, r in enumerate(neurons["root_id"].astype(str).to_numpy())}
            idx = np.array([by_id.get(r, -1) for r in vb["root_id"]])
            if (idx < 0).any():
                print(f"[flybrain] {int((idx < 0).sum())} viewer neurons not in the connectome; they stay dark")
            self.sample = torch.as_tensor(np.maximum(idx, 0), device=b.device)
            self.sample_ok = torch.as_tensor(idx >= 0, device=b.device)
            self.groups = vb["groups"]
            self.group_of = torch.as_tensor(vb["group"], device=b.device)
        else:
            print("[flybrain] dashboard/assets/flybrain/brain.json missing: run tools/fetch_flywire.py for the viewer")

    @classmethod
    def load(cls, data_dir: str | None = None, substeps: int = 20):
        data_dir = Path(data_dir or get("flybrain.data_dir", env="FLYBRAIN_DATA"))
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        if not (data_dir / "fly_neurons_real.csv").exists():
            print(f"[flybrain] no connectome in {data_dir} (run tools/fetch_flywire.py --synapses); placeholder controller in use")
            return None
        if not ((data_dir / PRINCETON).exists() or (data_dir / "fly_synapses_real.csv").exists()):
            print(f"[flybrain] neurons found but no synapse table in {data_dir} (tools/fetch_flywire.py --synapses); "
                  "placeholder controller in use")
            return None
        try:
            import torch
        except ImportError:
            print("[flybrain] torch not installed; placeholder controller in use")
            return None
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        # full wiring needs a GPU; on a CPU-only 16 GB laptop default to every 8th synapse
        stride = int(get("flybrain.synapse_stride", 0, env="FLYBRAIN_SYNAPSE_STRIDE")) or (1 if device.type == "cuda" else 8)
        print(f"[flybrain] loading connectome from {data_dir} on {device} (stride {stride}) ...")
        RealFlyBrain = _extract_class()
        conn, neurons, pr, dn, motion, n = build_connectome(data_dir, stride, device)
        print(f"[flybrain] {n:,} neurons, {conn._nnz():,} connections")
        upstream = RealFlyBrain(conn, neurons, pr, dn, motion, n, device, substeps=substeps)
        model = get("flybrain.model", "corrected", env="FLYBRAIN_MODEL")
        brain = CorrectedLIF(upstream, substeps) if model == "corrected" else upstream
        print(f"[flybrain] neuron model: {'corrected LIF (tau_m 20 ms)' if model == 'corrected' else 'upstream RealFlyBrain'}")
        adapter = cls(brain, neurons)
        adapter.stride, adapter.model = stride, model
        return adapter

    def step(self, flow: list[float]) -> dict:
        import torch
        self.fired_acc.zero_()
        motor, metrics = self.brain.compute(np.clip(np.asarray(flow, dtype=np.float32), 0, 1))
        if "yaw" in motor:                                     # upstream RealFlyBrain (Tello RC units)
            motor = {"forward": max(0.0, motor["forward"] / 100.0), "turn": motor["yaw"] / 100.0,
                     "climb": motor["vertical"] / 100.0}
        out = {
            "turn": float(motor["turn"]),
            "forward": float(motor.get("forward", 0.0)),
            "climb": float(motor.get("climb", 0.0)),
            "assisted": [k for k in ("forward", "climb") if k not in motor],
            "spikes": int(metrics["total_spikes"]),
            "source": "flywire",
            "model": getattr(self, "model", "upstream"),
            "synapse_stride": getattr(self, "stride", 1),
        }
        if self.sample is not None:
            hit = self.fired_acc[self.sample] & self.sample_ok
            out["fired"] = hit.nonzero().flatten().tolist()
            fired_per = torch.zeros(len(self.groups), device=hit.device).index_add_(0, self.group_of, hit.float())
            totals = torch.bincount(self.group_of, minlength=len(self.groups)).clamp(min=1).float()
            out["groups"] = {g: round(float(v), 3) for g, v in zip(self.groups, (fired_per / totals).tolist())}
        return out
