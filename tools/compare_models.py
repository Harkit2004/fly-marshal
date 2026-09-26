"""End-to-end comparison: rule baseline vs trained models, through the real brain.

Runs tools/evaluate.py on every session in both modes (in parallel) and prints one table.
Train sessions are marked: their numbers are optimistic (the final model saw them);
the held-out sessions are the honest result.

  python tools/compare_models.py --train vl_clean vl_spin ... --test spa_gt3_demo spa_example
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "e2e"


def run(session: str, rules: bool) -> dict:
    out = OUT / f"{session}.{'rules' if rules else 'model'}.json"
    cmd = [sys.executable, str(ROOT / "tools" / "evaluate.py"), str(ROOT / "data" / "sessions" / session),
           "--json", str(out)] + (["--rules"] if rules else [])
    log = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    (OUT / f"{session}.{'rules' if rules else 'model'}.txt").write_text(log.stdout + log.stderr)
    return json.loads(out.read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="*", default=[])
    ap.add_argument("--test", nargs="*", default=[])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(s, r) for s in args.train + args.test for r in (True, False)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(zip(jobs, ex.map(lambda j: run(*j), jobs)))

    def line(s, r):
        m = res[(s, r)]
        fa = m["false_alarms"] / max(m["minutes"], 1e-9)
        lat = f"{m['median_latency_s']:.2f}" if m["median_latency_s"] is not None else "  - "
        return (f"{m['detected']:3d}/{m['incidents']:<3d} {lat:>5s}s {fa:4.2f}/min "
                f"{m['warned']:3d} warned {m['predicted_followed_by_incident']:3d}/{m['predicted_warnings']:<3d} ok")

    print(f"{'session':18s} {'':6s} {'detected':>8s} latency  false alm  warned-before  warnings that came true")
    rows = []
    for group, names in [("held-out TEST", args.test), ("train (optimistic)", args.train)]:
        if not names:
            continue
        print(f"-- {group}")
        for s in names:
            for r in (True, False):
                print(f"{s:18s} {'rules' if r else 'model':6s} {line(s, r)}")
                rows.append({**res[(s, r)], "split": "test" if s in args.test else "train"})
    tot = {}
    for r in rows:
        k = (r["split"], r["mode"])
        t = tot.setdefault(k, {"incidents": 0, "detected": 0, "warned": 0, "false_alarms": 0, "minutes": 0.0})
        for f in t:
            t[f] += r[f]
    print("-- totals")
    for (split, mode), t in sorted(tot.items()):
        print(f"{split:5s} {mode:6s} detected {t['detected']}/{t['incidents']} "
              f"({t['detected'] / max(t['incidents'], 1):.0%})   warned before {t['warned']}   "
              f"false alarms {t['false_alarms'] / max(t['minutes'], 1e-9):.2f}/min")
    (OUT / "summary.json").write_text(json.dumps({"sessions": rows, "totals": {f"{a}_{b}": v for (a, b), v in tot.items()}}, indent=2))


if __name__ == "__main__":
    main()
