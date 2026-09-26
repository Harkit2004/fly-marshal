"""Load the real controller and check neural steering before starting the demo."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from drones.flybrain_real import RealFlyBrainAdapter


def main():
    brain = RealFlyBrainAdapter.load()
    if brain is None:
        raise SystemExit("Real fly unavailable; see the setup message above.")
    results = {}
    for name, flow in (("left", [0.3, 1, 0, 0]), ("right", [0.3, 0, 1, 0])):
        if hasattr(brain.brain, "reset_state"):
            brain.brain.reset_state()
        start = time.perf_counter()
        for _ in range(3):
            out = brain.step(flow)
        results[name] = {k: out[k] for k in ("source", "model", "synapse_stride", "turn", "spikes", "assisted")}
        results[name]["steps_per_second"] = round(3 / (time.perf_counter() - start), 2)
    print(json.dumps(results, indent=2), flush=True)
    if not (results["left"]["turn"] > 0 and results["right"]["turn"] < 0
            and all(r["spikes"] > 0 for r in results.values())):
        raise SystemExit("Neural steering check failed.")
    print("PASS: real neural activity responds in both steering directions.")


if __name__ == "__main__":
    main()
