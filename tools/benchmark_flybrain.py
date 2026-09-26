"""Compare old COO/indexed update with optimized CSR/where on identical inputs."""
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import torch
from drones.flybrain_real import RealFlyBrainAdapter


def baseline_step(b, drive):
    u = b.u
    b.ref = (b.ref - b.DT).clamp(min=0)
    syn = torch.sparse.mm(u.conn_T, b.spikes.float().unsqueeze(1)).squeeze()
    b.g = b.g * b.decay + syn * (b.W_SYN_MV * 1e-3)
    b.v = b.v + (u.V_rest - b.v + b.g + drive) * (b.DT / b.TAU_M)
    b.spikes = (b.v > u.V_thresh) & (b.ref <= 0)
    b.v[b.spikes] = u.V_rest
    b.ref[b.spikes] = u.tau_ref


def main():
    adapter = RealFlyBrainAdapter.load()
    if adapter is None or adapter.model != 'corrected':
        raise SystemExit('Requires the corrected real fly model.')
    b = adapter.brain
    optimized = type(b)._step.__get__(b)
    methods = {'before': lambda drive: baseline_step(b, drive), 'after': optimized}
    flows = [[.3, 1., 0., 0.], [.3, 0., 1., 0.], [.8, .2, .1, .4], [0., 0., 0., 0.]] * 3
    timings = {k: [] for k in methods}
    results = {}
    for name in ['before', 'after', 'after', 'before']:
        b._step = methods[name]
        b.reset_state()
        b.compute(flows[0])  # warm up kernels, excluded from timing
        b.reset_state()
        if b.device.type == 'cuda': torch.cuda.synchronize()
        start = time.perf_counter()
        outputs = [b.compute(flow) for flow in flows]
        if b.device.type == 'cuda': torch.cuda.synchronize()
        timings[name].append((time.perf_counter() - start) * 1000 / len(flows))
        results[name] = (outputs, b.v.cpu().clone(), b.g.cpu().clone(), b.spikes.cpu().clone())
    for key in (1, 2):
        torch.testing.assert_close(results['before'][key], results['after'][key], rtol=1e-4, atol=1e-7)
    assert results['before'][0] == results['after'][0], 'Motor/spike metrics changed'
    assert torch.equal(results['before'][3], results['after'][3]), 'Spike state changed'
    summary = {'device': str(b.device), 'neurons': b.n_neurons, 'synapse_stride': adapter.stride,
               'ms_per_step': {k: statistics.median(v) for k, v in timings.items()},
               'samples_ms': timings, 'equivalence': 'PASS: same motor outputs, spike metrics and spike state; voltages within tolerance'}
    summary['speedup'] = summary['ms_per_step']['before'] / summary['ms_per_step']['after']
    path = Path(__file__).resolve().parent.parent / 'data/runtime/fly-performance.json'
    path.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__': main()
