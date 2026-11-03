"""Loads the rulebook and calendar, and knows where every file lives."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("BOT_ROOT", Path(__file__).resolve().parent.parent))
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "state"
DATA_DIR = ROOT / "data"
PLANS_DIR = ROOT / "plans"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"


def load_rules() -> dict:
    with open(CONFIG_DIR / "rules.yaml") as f:
        return yaml.safe_load(f)


def load_calendar() -> dict:
    with open(CONFIG_DIR / "calendar.yaml") as f:
        cal = yaml.safe_load(f) or {}
    cal["holidays"] = {_d(x) for x in cal.get("holidays") or []}
    cal["no_trade_days"] = {_d(x) for x in cal.get("no_trade_days") or []}
    cal["blackout"] = {
        k: (_d(v[0]), _d(v[1])) for k, v in (cal.get("blackout") or {}).items()
    }
    return cal


def _d(x) -> dt.date:
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    tmp.replace(path)
