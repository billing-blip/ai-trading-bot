import datetime as dt

import numpy as np
import pandas as pd
import pytest

from bot import checkin, planner, simulator
from bot import portfolio as P
from bot.charges import round_trip
from bot.config import load_calendar, load_rules
from bot.indicators import add_all
from bot.risk import apply_loss_limits, can_add, open_risk, size_trade
from bot.strategies import breakout, orb_setup, trend_pullback
from bot.timeutil import IST, at

DAY = dt.date(2026, 11, 4)  # a Wednesday, trading day
RULES = load_rules()
CAL = load_calendar()


def bars(day, rows):
    """rows: list of ("HH:MM", open, high, low, close)"""
    idx = [at(day, r[0]) for r in rows]
    return pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
                         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows],
                         "Volume": [1000] * len(rows)}, index=pd.DatetimeIndex(idx))


def fresh_pf():
    return P.new_portfolio(RULES)


def order(**kw):
    o = {"id": "o1", "symbol": "ABC", "strategy": "breakout", "type": "swing", "entry": 100.0,
         "stop": 98.0, "target": 104.0, "qty": 100, "risk_amount": 200.0, "position_value": 10000.0,
         "last_processed": at(DAY, "09:00").isoformat()}
    o.update(kw)
    return o


# ---------------- charges & sizing ----------------

def test_charges_reasonable():
    intraday = round_trip(25000, 25100, True, RULES["charges"])
    delivery = round_trip(25000, 25100, False, RULES["charges"])
    assert 10 < intraday < 40
    assert 50 < delivery < 90   # STT 0.1% both sides dominates


def test_sizing_never_exceeds_max_loss():
    for entry, stop in [(100, 98), (2500, 2440), (55, 54.2), (1200, 1199)]:
        s = {"entry": entry, "stop": stop, "target": entry + 2 * (entry - stop), "type": "swing"}
        sz = size_trade(s, RULES, 70000)
        if sz:
            assert sz["max_loss"] <= RULES["risk"]["max_loss_per_trade"]
            assert sz["position_value"] <= RULES["risk"]["max_position_value"] + 1


# ---------------- simulator ----------------

def run_sim(pf, rows, now="15:31", sym="ABC"):
    return simulator.run(pf, RULES, {}, DAY, {sym: bars(DAY, rows)}, at(DAY, now))


def test_entry_then_target():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:30", 99.5, 100.2, 99.4, 100.1), ("09:35", 100.1, 104.5, 100.0, 104.2)])
    assert not pf["pending"] and not pf["positions"]
    t = pf["closed"][0]
    assert t["exit_reason"] == "target hit" and t["net_pnl"] > 0


def test_stop_wins_when_both_touched():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:30", 99.5, 100.2, 99.4, 100.1), ("09:35", 100.0, 105.0, 97.0, 101.0)])
    assert pf["closed"][0]["exit_reason"].startswith("stop")


def test_gap_through_stop_exits_at_open():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:30", 99.5, 100.2, 99.4, 100.1), ("09:35", 96.0, 96.5, 95.5, 96.2)])
    t = pf["closed"][0]
    assert t["exit_reason"] == "stop-loss (gapped through)" and t["exit_price"] < 96.1


def test_breakeven_move():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:30", 99.5, 100.2, 99.4, 100.1), ("09:35", 100.1, 102.3, 100.0, 102.0),
                 ("09:40", 102.0, 102.1, 100.0, 100.2)])
    t = pf["closed"][0]
    assert t["exit_reason"] == "breakeven stop"


def test_stock_gap_rule_cancels():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:15", 103.0, 103.5, 102.8, 103.1), ("09:30", 103.0, 103.5, 102.8, 103.1)])
    assert not pf["pending"] and not pf["positions"]
    assert any("gap rule" in e["detail"] for e in pf["events"])


def test_no_swing_entry_before_930():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:15", 99.0, 100.5, 98.9, 100.2)], now="09:21")
    assert pf["pending"] and not pf["positions"]


def test_intraday_square_off():
    pf = fresh_pf()
    pf["pending"].append(order(type="intraday", strategy="opening_range_breakout", entry=100, stop=99, target=102,
                               active_from=at(DAY, "09:45").isoformat()))
    run_sim(pf, [("09:50", 99.9, 100.3, 99.8, 100.2), ("15:15", 100.5, 100.6, 100.4, 100.5)])
    assert pf["closed"][0]["exit_reason"] == "intraday square-off 15:15"


def test_late_check_still_catches_fills():
    """A run that happens late still processes earlier candles correctly."""
    pf = fresh_pf()
    pf["pending"].append(order())
    rows = [("09:30", 99.5, 100.2, 99.4, 100.1), ("10:00", 100.1, 101.0, 97.5, 98.0)]
    run_sim(pf, rows, now="12:00")
    assert pf["closed"][0]["exit_reason"] == "stop-loss"


def test_daily_loss_limit_blocks_and_cancels():
    pf = fresh_pf()
    big = order(qty=600, risk_amount=1200, position_value=60000, stop=98.0)
    pf["pending"] += [big, order(id="o2", symbol="XYZ")]
    c1 = bars(DAY, [("09:30", 99.5, 100.2, 99.4, 100.1), ("09:35", 100.0, 100.0, 97.9, 98.0)])
    c2 = bars(DAY, [("09:45", 99.0, 99.5, 98.9, 99.2)])
    simulator.run(pf, RULES, {}, DAY, {"ABC": c1, "XYZ": c2}, at(DAY, "15:31"))
    assert pf["blocks"].get("day_blocked") == DAY.isoformat()
    assert not pf["pending"]


def test_can_add_limits():
    pf = fresh_pf()
    for i in range(4):
        pf["pending"].append(order(id=f"x{i}", symbol=f"S{i}", risk_amount=400))
    assert can_add(pf, RULES, order(id="new", symbol="NEW"), DAY) == "max open swing trades reached"
    pf = fresh_pf()
    pf["pending"].append(order(risk_amount=1900, qty=950))
    assert "risk" in can_add(pf, RULES, order(id="n2", symbol="N2", risk_amount=200), DAY)


# ---------------- strategies ----------------

def synthetic_daily(n=120, breakout_last=True, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.25, 1.0, n))
    df = pd.DataFrame({"Open": close - 0.3, "High": close + 1, "Low": close - 1, "Close": close,
                       "Volume": np.full(n, 1e6)},
                      index=[DAY - dt.timedelta(days=n - i) for i in range(n)])
    if breakout_last:
        top = df["High"].iloc[-21:-1].max()
        df.iloc[-1, df.columns.get_loc("Close")] = top + 2
        df.iloc[-1, df.columns.get_loc("High")] = top + 2.5
        df.iloc[-1, df.columns.get_loc("Volume")] = 3e6
    return df


def test_breakout_detected():
    ind = add_all(synthetic_daily(), RULES)
    s = breakout("ABC", ind, RULES)
    assert s and s["target"] - s["entry"] >= 2 * (s["entry"] - s["stop"]) - 0.1


def test_orb_setup_and_range_filter():
    rows = [(f"09:{m:02d}", 100, 100.4, 99.8, 100.1) for m in range(15, 45, 5)]
    s = orb_setup("ABC", bars(DAY, rows), DAY, RULES)
    assert s and s["entry"] > 100.4 and s["stop"] <= 99.8
    wide = [(f"09:{m:02d}", 100, 103, 99, 100) for m in range(15, 45, 5)]
    assert orb_setup("ABC", bars(DAY, wide), DAY, RULES) is None


# ---------------- plan validation ----------------

SNAP = {"date": DAY.isoformat(),
        "swing_candidates": [{"id": f"BR-S{i}", "symbol": f"S{i}"} for i in range(7)],
        "orb_eligible": [{"symbol": "OR1"}]}


def test_plan_ok():
    plan = {"date": DAY.isoformat(), "regime": "trending_up",
            "selected": [{"id": "BR-S1", "priority": 1, "reason": "clean breakout"}],
            "orb_selected": [{"symbol": "OR1", "reason": "strong"}]}
    errors, cleaned = planner.validate_plan(plan, SNAP, RULES)
    assert not errors and len(cleaned["selected"]) == 1


def test_plan_rejects_invented_and_too_many():
    plan = {"date": DAY.isoformat(), "regime": "sideways",
            "selected": [{"id": f"BR-S{i}", "priority": i, "reason": "x"} for i in range(6)] +
                        [{"id": "FAKE", "reason": "x"}],
            "orb_selected": [{"symbol": "OR1", "reason": "x"}]}
    errors, _ = planner.validate_plan(plan, SNAP, RULES)
    assert any("FAKE" in e for e in errors)
    assert any("more than" in e for e in errors)
    assert any("ORB" in e for e in errors)


def test_high_risk_means_no_trade():
    plan = {"date": DAY.isoformat(), "regime": "high_risk",
            "selected": [{"id": "BR-S1", "reason": "x"}]}
    errors, cleaned = planner.validate_plan(plan, SNAP, RULES)
    assert not errors and cleaned["no_trade"] and not cleaned["selected"]


# ---------------- AI check-in guard rails ----------------

def test_checkin_only_reduces_risk():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:30", 99.5, 100.2, 99.4, 100.1)], now="09:40")
    pos = pf["positions"][0]
    now = at(DAY, "10:50")
    reply = {"actions": [
        {"type": "tighten_stop", "id": pos["id"], "new_stop": 90, "reason": "widen (must be rejected)"},
        {"type": "buy_more", "id": pos["id"], "reason": "not allowed"},
        {"type": "tighten_stop", "id": pos["id"], "new_stop": 99.0, "reason": "lock in"},
    ]}
    done = checkin.apply_actions(pf, RULES, reply, now, DAY)
    assert pos["stop"] == 99.0 and len(done) == 1
    done = checkin.apply_actions(pf, RULES, {"actions": [{"type": "exit", "id": pos["id"], "reason": "bad news"}]}, now, DAY)
    assert not pf["positions"] and pf["closed"][0]["exit_reason"].startswith("AI early exit")


# ---------------- end of day ----------------

def test_eod_time_exit_and_cancel():
    pf = fresh_pf()
    pf["pending"].append(order())
    run_sim(pf, [("09:30", 99.5, 100.2, 99.4, 100.1)], now="09:40")
    pf["positions"][0]["days_held"] = RULES["trade_rules"]["swing_max_hold_days"] - 1
    pf["pending"].append(order(id="o9", symbol="ZZZ"))
    simulator.end_of_day(pf, RULES, DAY, {"ABC": 101.0}, at(DAY, "16:00"))
    assert not pf["positions"] and not pf["pending"]
    assert pf["closed"][0]["exit_reason"].startswith("time exit")
