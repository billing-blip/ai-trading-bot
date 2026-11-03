"""Hard risk limits. Nothing in here can be overridden by the AI."""
from __future__ import annotations

import datetime as dt
import math

from .charges import round_trip, slip
from .timeutil import month_start, week_start


def size_trade(setup: dict, rules: dict, capital_available: float) -> dict | None:
    """Pick the share quantity so that a stop-out loses at most `max_loss_per_trade`
    INCLUDING charges and slippage. Returns sizing info or None if the trade can't fit."""
    r, c = rules["risk"], rules["charges"]
    intraday = setup["type"] == "intraday"
    entry_fill = slip(setup["entry"], "buy", c)
    stop_fill = slip(setup["stop"], "sell", c)
    loss_ps = entry_fill - stop_fill
    if loss_ps <= 0:
        return None
    cap_value = min(r["max_position_value"], capital_available)
    qty = min(math.floor(r["max_loss_per_trade"] / loss_ps), math.floor(cap_value / entry_fill))
    while qty >= 1:
        ch = round_trip(qty * entry_fill, qty * stop_fill, intraday, c)
        worst = qty * loss_ps + ch
        if worst <= r["max_loss_per_trade"]:
            reward = qty * (slip(setup["target"], "sell", c) - entry_fill) - round_trip(
                qty * entry_fill, qty * setup["target"], intraday, c)
            return {
                "qty": qty,
                "position_value": round(qty * setup["entry"], 2),
                "max_loss": round(worst, 2),
                "est_profit_at_target": round(reward, 2),
                "risk_amount": round(qty * (setup["entry"] - setup["stop"]), 2),
            }
        qty -= 1
    return None


def refit_qty(order: dict, fill_price: float, rules: dict) -> int:
    """If the price jumped past our entry, buying the planned quantity could lose more than
    the limit at the stop. Shrink the quantity so the ₹ risk still fits (0 = skip)."""
    if fill_price <= order["entry"]:
        return order["qty"]
    s = {**order, "entry": fill_price}
    sz = size_trade(s, rules, order["qty"] * fill_price + 1)
    return min(order["qty"], sz["qty"]) if sz else 0


# ---------- portfolio-level checks ----------

def open_risk(pf: dict) -> float:
    total = 0.0
    for p in pf["positions"]:
        total += max(0.0, (p["entry_price"] - p["stop"])) * p["qty"]
    for o in pf["pending"]:
        total += max(0.0, (o["entry"] - o["stop"])) * o["qty"]
    return round(total, 2)


def capital_in_use(pf: dict, kind: str) -> float:
    used = sum(p["entry_price"] * p["qty"] for p in pf["positions"] if p["type"] == kind)
    used += sum(o["entry"] * o["qty"] for o in pf["pending"] if o["type"] == kind)
    return used


def bucket_available(pf: dict, rules: dict, kind: str) -> float:
    cap = rules["capital"]["swing_bucket" if kind == "swing" else "intraday_bucket"]
    return max(0.0, min(cap - capital_in_use(pf, kind), pf["cash"] - sum(
        o["entry"] * o["qty"] for o in pf["pending"])))


def intraday_count_today(pf: dict, day: dt.date) -> int:
    n = sum(1 for t in pf["closed"] if t["type"] == "intraday" and t["entry_time"][:10] == day.isoformat())
    n += sum(1 for p in pf["positions"] if p["type"] == "intraday")
    n += sum(1 for o in pf["pending"] if o["type"] == "intraday")
    return n


def blocked_reason(pf: dict, control: dict, day: dt.date) -> str | None:
    b = pf["blocks"]
    if control.get("paused"):
        return "paused by owner"
    if b.get("month_halted"):
        return "monthly loss limit hit (full stop until reviewed)"
    if b.get("week_blocked_until") and day < dt.date.fromisoformat(b["week_blocked_until"]):
        return "weekly loss limit hit"
    if b.get("day_blocked") == day.isoformat():
        return "daily loss limit hit"
    if b.get("no_trade_day") == day.isoformat():
        return pf["blocks"].get("no_trade_reason") or "no-trade day"
    return None


def can_add(pf: dict, rules: dict, order: dict, day: dt.date) -> str | None:
    """Return None if the order may be added, else the reason it can't."""
    r = rules["risk"]
    if order["type"] == "swing":
        n_swing = sum(1 for p in pf["positions"] if p["type"] == "swing") + sum(
            1 for o in pf["pending"] if o["type"] == "swing")
        if n_swing >= r["max_open_swing_trades"]:
            return "max open swing trades reached"
        if any(p["symbol"] == order["symbol"] for p in pf["positions"]):
            return "already holding this stock"
    else:
        if intraday_count_today(pf, day) >= r["max_intraday_trades_per_day"]:
            return "max intraday trades for today reached"
    if open_risk(pf) + order["risk_amount"] > r["max_total_open_risk"] + 1e-6:
        return "total open risk limit reached"
    if order["position_value"] > r["max_position_value"] + 1e-6:
        return "position too large"
    return None


# ---------- loss limits ----------

def equity(pf: dict, rules: dict) -> float:
    c = rules["charges"]
    eq = pf["cash"]
    for p in pf["positions"]:
        px = p.get("last_price", p["entry_price"])
        eq += px * p["qty"] - round_trip(p["entry_price"] * p["qty"], px * p["qty"], p["type"] == "intraday", c)
    return round(eq, 2)


def _equity_on_or_before(pf: dict, day: dt.date) -> float:
    """Equity at the close of the last recorded day strictly before `day`."""
    best = pf["starting_capital"]
    for rec in pf["equity_history"]:
        if dt.date.fromisoformat(rec["date"]) < day:
            best = rec["equity"]
    return best


def period_pnl(pf: dict, rules: dict, day: dt.date) -> dict:
    eq = equity(pf, rules)
    return {
        "equity": eq,
        "day": round(eq - _equity_on_or_before(pf, day), 2),
        "week": round(eq - _equity_on_or_before(pf, week_start(day)), 2),
        "month": round(eq - _equity_on_or_before(pf, month_start(day)), 2),
    }


def apply_loss_limits(pf: dict, rules: dict, day: dt.date) -> list[str]:
    """Update blocks if a loss limit is hit. Returns list of newly triggered limits."""
    r = rules["risk"]
    pnl = period_pnl(pf, rules, day)
    hit = []
    b = pf["blocks"]
    if pnl["month"] <= -r["monthly_loss_limit"] and not b.get("month_halted"):
        b["month_halted"] = True
        hit.append(f"MONTHLY loss limit hit ({pnl['month']:.0f}) — full stop until reviewed")
    nxt_monday = week_start(day) + dt.timedelta(days=7)
    if pnl["week"] <= -r["weekly_loss_limit"] and b.get("week_blocked_until") != nxt_monday.isoformat():
        b["week_blocked_until"] = nxt_monday.isoformat()
        hit.append(f"WEEKLY loss limit hit ({pnl['week']:.0f}) — no new trades until {nxt_monday}")
    if pnl["day"] <= -r["daily_loss_limit"] and b.get("day_blocked") != day.isoformat():
        b["day_blocked"] = day.isoformat()
        hit.append(f"DAILY loss limit hit ({pnl['day']:.0f}) — no new trades today")
    return hit
