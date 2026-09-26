"""settings.toml at the repo root -> nested dict, with defaults for anything left out.

    from shared.settings import get
    get("drones.max_speed_mps")          # 25.0
    get("flybrain.model", env="FLYBRAIN_MODEL")   # env var wins when set
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """KEY=value lines from .env into the environment (real env vars win). See .env.example."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #")[0].strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


_load_dotenv()
PATH = Path(os.environ.get("MARSHAL_SETTINGS", ROOT / "settings.toml"))

DEFAULTS = {
    "live": {"ac_root": "C:/Program Files (x86)/Steam/steamapps/common/assettocorsa",
             "delay_s": 2.0, "track_source": "auto"},
    "detection": {"anomaly_on": 0.5, "anomaly_ticks": 8, "predict_ttl_s": 8.0, "incident_max_s": 90.0},
    "drones": {"count": 3, "max_speed_mps": 25.0, "max_accel_mps2": 8.0, "min_alt_m": 15.0,
               "max_alt_m": 60.0, "patrol_alt_m": 25.0, "min_separation_m": 10.0,
               "track_clearance_m": 12.0, "standoff_m": 18.0, "hold_alt_m": 22.0,
               "escort_back_m": 25.0, "versus": True, "versus_stack_m": 14.0},
    "flybrain": {"data_dir": "data/flywire", "model": "corrected", "synapse_stride": 0, "steer_assist": 0.0},
    "vision": {"enabled": False, "provider": "openai", "model": "gpt-6-luna",
               "api_key_env": "OPENAI_API_KEY", "cache_dir": "cv/cache", "timeout_s": 8.0},
    "scene": {},          # dashboard has its own defaults
    "models": {},
    "tracks": {},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load(path: Path = PATH) -> dict:
    if not path.exists():
        return DEFAULTS
    with open(path, "rb") as f:
        return _merge(DEFAULTS, tomllib.load(f))


SETTINGS = load()


def get(key: str, default=None, env: str | None = None):
    """Dotted lookup; `env` names an environment variable that overrides the file."""
    if env and os.environ.get(env):
        raw = os.environ[env]
        base = get(key, default)
        return type(base)(raw) if isinstance(base, (int, float)) and not isinstance(base, bool) else raw
    node = SETTINGS
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def dashboard_settings() -> dict:
    """The parts the browser needs; sent inside the "track" message."""
    return {"scene": SETTINGS.get("scene", {}), "models": SETTINGS.get("models", {}),
            "tracks": SETTINGS.get("tracks", {})}
