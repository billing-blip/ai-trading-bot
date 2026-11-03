"""The paper-trading account: cash, open positions, pending orders, trade history."""
from __future__ import annotations

import datetime as dt
import uuid

from .charges import round_trip, slip
from .config import STATE_DIR, read_json, write_json
from .timeutil import now_ist

PF_PATH = STATE_DIR / "portfolio.json"
CONTROL_PATH = STATE_DIR / "control.json"


def new_portfolio(rules: dict) -> dict:
    cap = rules["capital"]["starting"]
    return {
        "starting_capital": cap,
        "cash": cap,
        "positions": [],
        "pending": [],
        "closed": [],
        "events": [],
        "blocks": {},
        "equity_history": [],
        "created": now_ist().isoformat(),
    }


def load(rules: dict) -> dict:
    pf = read_json(PF_PATH)
    return pf if pf else new_portfolio(rules)


def save(pf: dict) -> None:
    write_json(PF_PATH, pf)


def load_control() -> dict:
    return read_json(CONTROL_PATH, {"paused": False})


def log(pf: dict, kind: str, detail: str, when: dt.datetime | None = None) -> None:
    pf["events"].append({"time": (when or now_ist()).isoformat(), "kind": kind, "detail": detail})
    pf["events"] = pf["events"][-2000:]


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def open_position(pf: dict, order: dict, price: float, when: dt.datetime, rules: dict) -> dict:
    fill = round(slip(price, "buy", rules["charges"]), 2)
    pos = {
        "id": order["id"],
        "symbol": order["symbol"],
        "strategy": order["strategy"],
        "type": order["type"],
        "qty": order["qty"],
        "entry_price": fill,
        "entry_time": when.isoformat(),
        "stop": order["stop"],
        "initial_stop": order["stop"],
        "target": order["target"],
        "initial_risk_ps": round(fill - order["stop"], 2),
        "days_held": 0,
        "last_price": fill,
        "last_processed": when.isoformat(),
        "plan_date": order.get("plan_date"),
        "reason": order.get("reason", ""),
        "notes": order.get("notes", ""),
    }
    pf["cash"] = round(pf["cash"] - fill * pos["qty"], 2)
    pf["positions"].append(pos)
    log(pf, "ENTRY", f"BUY {pos['qty']} {pos['symbol']} @ {fill} ({pos['strategy']}, stop {pos['stop']}, target {pos['target']})", when)
    return pos


def close_position(pf: dict, pos: dict, price: float, when: dt.datetime, reason: str, rules: dict) -> dict:
    c = rules["charges"]
    fill = round(slip(price, "sell", c), 2)
    charges = round_trip(pos["entry_price"] * pos["qty"], fill * pos["qty"], pos["type"] == "intraday", c)
    gross = round((fill - pos["entry_price"]) * pos["qty"], 2)
    net = round(gross - charges, 2)
    pf["cash"] = round(pf["cash"] + fill * pos["qty"] - charges, 2)
    pf["positions"] = [p for p in pf["positions"] if p["id"] != pos["id"]]
    trade = {**pos, "exit_price": fill, "exit_time": when.isoformat(), "exit_reason": reason,
             "gross_pnl": gross, "charges": charges, "net_pnl": net,
             "r_multiple": round(net / (pos["initial_risk_ps"] * pos["qty"]), 2) if pos["initial_risk_ps"] > 0 else None}
    pf["closed"].append(trade)
    log(pf, "EXIT", f"SELL {pos['qty']} {pos['symbol']} @ {fill} — {reason} — net {net:+.2f}", when)
    return trade
