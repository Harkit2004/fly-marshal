"""Install only Marshal Drone Cams; never alter the existing VRC logger."""
from pathlib import Path
import argparse
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.settings import ROOT, get


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ac-root", type=Path, default=Path(get("live.ac_root", env="AC_ROOT")))
    args = ap.parse_args()
    root = args.ac_root.resolve()
    if not (root / "acs.exe").exists():
        ap.error("AC root does not contain acs.exe")
    source = ROOT / "ac_apps" / "marshal_drone_cams"
    target = root / "apps" / "lua" / "marshal_drone_cams"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.ini", "marshal_drone_cams.lua", "yellow_control.lua", "yellow_flag.lua"):
        src, dst = source / name, target / name
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            shutil.copy2(dst, dst.with_suffix(dst.suffix + ".bak"))
        shutil.copy2(src, dst)
    print(f"Installed: {target}")
    print("Restart the AC session, then open Marshal Drone Cams from the Lua apps taskbar.")


if __name__ == "__main__":
    main()
