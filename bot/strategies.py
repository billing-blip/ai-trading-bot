"""The strategy menu. The AI chooses from these; it never invents prices.

Each function looks at completed daily candles (last row = last finished trading day)
and returns a trade *setup* for the next session, or None.
"""
from __future__ import annotations

import math

import pandas as pd

TICK = 0.05


def tick_up(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 2)


def tick_down(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 2)


def _setup(symbol, strategy, kind, entry, stop, rr, notes):
    entry, stop = tick_up(entry), tick_down(stop)
    risk = entry - stop
    if risk <= 0 or risk / entry > 0.06:      # skip stops wider than 6%
        return None
    target = tick_up(entry + rr * risk)
    return {
        "symbol": symbol,
        "strategy": strategy,
        "type": kind,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_per_share": round(risk, 2),
        "notes": notes,
    }


def trend_pullback(symbol: str, df: pd.DataFrame, rules: dict):
    p = rules["strategies"]["trend_pullback"]
    if not p["enabled"] or len(df) < p["trend_sma"] + 6:
        return None
    last = df.iloc[-1]
    if pd.isna(last["sma_trend"]) or pd.isna(last["sma_pull"]):
        return None
    uptrend = last["Close"] > last["sma_trend"] and df["sma_trend"].iloc[-1] > df["sma_trend"].iloc[-6]
    recent = df.iloc[-p["stop_lookback_days"]:]
    touched = (recent["Low"] <= recent["sma_pull"] * (1 + p["pullback_touch_pct"] / 100)).any()
    bounced = last["Close"] > last["sma_pull"]
    if not (uptrend and touched and bounced):
        return None
    entry = last["High"] * 1.001
    stop = recent["Low"].min() * 0.998
    return _setup(symbol, "trend_pullback", "swing", entry, stop,
                  rules["risk"]["min_reward_to_risk"],
                  f"Uptrend (above {p['trend_sma']}-day avg), dipped to {p['pullback_sma']}-day avg and bounced")


def breakout(symbol: str, df: pd.DataFrame, rules: dict):
    p = rules["strategies"]["breakout"]
    if not p["enabled"] or len(df) < max(p["lookback_days"], rules["strategies"]["trend_pullback"]["trend_sma"]) + 2:
        return None
    last, prev = df.iloc[-1], df.iloc[-2]
    if pd.isna(prev["high_n"]) or pd.isna(prev["vol_avg"]) or pd.isna(last["atr"]):
        return None
    broke_out = last["Close"] > prev["high_n"]
    volume_ok = last["Volume"] >= p["volume_multiple"] * prev["vol_avg"]
    uptrend = pd.notna(last["sma_trend"]) and last["Close"] > last["sma_trend"]
    if not (broke_out and volume_ok and uptrend):
        return None
    entry = last["High"] * 1.001
    stop = entry - p["atr_stop_multiple"] * last["atr"]
    return _setup(symbol, "breakout", "swing", entry, stop,
                  rules["risk"]["min_reward_to_risk"],
                  f"Closed above {p['lookback_days']}-day high on {last['Volume'] / prev['vol_avg']:.1f}x normal volume")


def orb_eligible(symbol: str, df: pd.DataFrame, rules: dict):
    """Pre-market: is this stock a sensible opening-range-breakout candidate today?"""
    p = rules["strategies"]["opening_range_breakout"]
    if not p["enabled"] or len(df) < 60:
        return None
    last = df.iloc[-1]
    if pd.isna(last["sma_trend"]) or pd.isna(last["sma_pull"]):
        return None
    if last["Close"] > last["sma_pull"] > last["sma_trend"]:
        return {"symbol": symbol, "strategy": "opening_range_breakout", "type": "intraday",
                "notes": "Strong uptrend (price > 20-day avg > 50-day avg); entry set after first 30 min"}
    return None


def orb_setup(symbol: str, candles: pd.DataFrame, day, rules: dict):
    """Intraday: build the ORB order from the first 30 minutes of today's 5-min candles."""
    p = rules["strategies"]["opening_range_breakout"]
    from .timeutil import at  # local import to avoid cycle

    start = at(day, "09:15")
    end = start + pd.Timedelta(minutes=p["range_minutes"])
    rng = candles[(candles.index >= start) & (candles.index < end)]
    expected = p["range_minutes"] // 5
    if len(rng) < expected:
        return None
    hi, lo = rng["High"].max(), rng["Low"].min()
    if (hi - lo) / hi * 100 > p["max_range_pct"]:
        return None
    s = _setup(symbol, "opening_range_breakout", "intraday", hi * 1.0005, lo,
               rules["risk"]["min_reward_to_risk"],
               f"First {p['range_minutes']} min range {lo:.2f}-{hi:.2f}")
    if s:
        s["active_from"] = end.isoformat()
    return s


SWING_STRATEGIES = [trend_pullback, breakout]
