"""Momentum rotation — the strategy that passed testing (variant I).

Runs once per trading day AFTER the close (one GitHub job). For each finished trading day
it has not processed yet (so a late or missed run catches up by itself) it:
  1. fills the orders decided the evening before, at that day's OPEN price (+ slippage)
  2. checks each holding's 12% stop-loss against the day's low
  3. marks the account to the day's close
  4. on the last trading day of the month (or on the very first run): ranks Nifty 50 by
     12-month return, keeps stocks above their 200-day average, picks the top 5 —
     or goes to CASH if Nifty is below its 200-day average — and queues the orders
     for the next morning
  5. records the day for the dashboard and reports
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

from . import llm
from . import portfolio as P
from .config import STATE_DIR, read_json, write_json
from .news import headlines
from .risk import equity
from .timeutil import IST, is_trading_day, now_ist

STATE_PATH = STATE_DIR / "momentum.json"


def _ts(day, hh, mm):
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST)


def last_completed_day(now: dt.datetime, cal: dict) -> dt.date:
    d = now.date()
    if not (is_trading_day(d, cal) and now.time() >= dt.time(15, 45)):
        d -= dt.timedelta(days=1)
        while not is_trading_day(d, cal):
            d -= dt.timedelta(days=1)
    return d


def next_trading_day(d: dt.date, cal: dict) -> dt.date:
    d += dt.timedelta(days=1)
    while not is_trading_day(d, cal):
        d += dt.timedelta(days=1)
    return d


def rank(closes: pd.DataFrame, day, m: dict) -> pd.DataFrame:
    hist = closes.loc[:day]
    rows = []
    for s in hist.columns:
        c = hist[s].dropna()
        if len(c) < m["lookback_days"] + 1 or len(c) < m["stock_sma"]:
            continue
        ret = c.iloc[-1] / c.iloc[-1 - m["lookback_days"]] - 1
        above = bool(c.iloc[-1] > c.iloc[-m["stock_sma"]:].mean())
        rows.append({"symbol": s, "return_12m_pct": round(ret * 100, 1), "above_200d": above,
                     "close": round(float(c.iloc[-1]), 2)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("return_12m_pct", ascending=False).reset_index(drop=True)


def ai_review(candidates: list[str], rules: dict) -> dict[str, str]:
    """AI may only EXCLUDE a pick (serious negative news / results tomorrow). Returns {symbol: reason}."""
    if not llm.configured() or not candidates:
        return {}
    lines = []
    for s in candidates:
        for h in headlines(f"{s} share NSE", 4):
            lines.append(f"[{s}] {h['title']} ({h['published']})")
    system = ("You review stocks a rule-based momentum strategy is about to buy for a month. You may only EXCLUDE "
              "a stock for a clear, serious reason in the headlines: fraud/governance scandal, regulator action, "
              "default, or quarterly results due in the next 2 days. Ordinary news is NOT a reason. "
              'Reply ONLY with JSON: {"exclude": [{"symbol": "...", "reason": "..."}]}')
    try:
        reply = llm.extract_json(llm.chat(system, "Candidates: " + ", ".join(candidates) + "\n\n" + "\n".join(lines),
                                          rules["ai"]["checkin_model"], 600))
        return {e["symbol"]: str(e.get("reason", ""))[:200] for e in reply.get("exclude", [])
                if isinstance(e, dict) and e.get("symbol") in candidates}
    except Exception:
        return {}


def process_day(pf, st, rules, cal, day, opens, highs, lows, closes, nifty, use_ai=True) -> list[str]:
    m = rules["momentum"]
    msgs = []
    o_t, c_t = _ts(day, 9, 15), _ts(day, 15, 30)

    # 1) fill orders queued last evening, sells first
    pend = pf.get("pending", [])
    pf["pending"] = []
    for o in [x for x in pend if x["side"] == "sell"]:
        pos = next((p for p in pf["positions"] if p["symbol"] == o["symbol"]), None)
        px = opens.at[day, o["symbol"]] if o["symbol"] in opens.columns else None
        if pos and pd.notna(px):
            t = P.close_position(pf, pos, float(px), o_t, o["reason"], rules)
            msgs.append(f"SOLD {t['symbol']} {t['net_pnl']:+.0f}")
    buys = [x for x in pend if x["side"] == "buy"]
    for o in buys:
        px = opens.at[day, o["symbol"]] if o["symbol"] in opens.columns else None
        if pd.isna(px) or px <= 0:
            P.log(pf, "SKIP", f"{o['symbol']} no opening price — buy skipped", o_t)
            continue
        qty = math.floor(min(o["amount"], pf["cash"] * 0.995) / (px * 1.001))
        if qty < 1:
            continue
        stop = round(px * (1 - m["stop_pct"] / 100), 2) if m.get("stop_pct") else None
        pos = P.open_position(pf, {"id": P.new_id(), "symbol": o["symbol"], "strategy": "momentum_12m",
                                   "type": "swing", "qty": qty, "stop": stop or 0.01, "target": None,
                                   "reason": o["reason"]}, float(px), o_t, rules)
        msgs.append(f"BOUGHT {qty} {pos['symbol']} @ {pos['entry_price']}")

    # 2) stop-losses on today's bar
    for p in list(pf["positions"]):
        s = p["symbol"]
        if s not in lows.columns or not m.get("stop_pct"):
            continue
        op, lo = opens.at[day, s], lows.at[day, s]
        if pd.notna(op) and op <= p["stop"]:
            t = P.close_position(pf, p, float(op), c_t, "12% stop-loss (gapped through)", rules)
        elif pd.notna(lo) and lo <= p["stop"]:
            t = P.close_position(pf, p, p["stop"], c_t, "12% stop-loss", rules)
        else:
            continue
        msgs.append(f"STOP {t['symbol']} {t['net_pnl']:+.0f}")

    # 3) mark to market
    for p in pf["positions"]:
        c = closes.at[day, p["symbol"]] if p["symbol"] in closes.columns else None
        if pd.notna(c):
            p["last_price"] = round(float(c), 2)
        p["days_held"] = p.get("days_held", 0) + 1
    eq = equity(pf, rules)
    st["peak_equity"] = max(st.get("peak_equity", pf["starting_capital"]), eq)
    pf["equity_history"] = [r for r in pf["equity_history"] if r["date"] != day.isoformat()]
    pf["equity_history"].append({"date": day.isoformat(), "equity": eq, "cash": pf["cash"],
                                 "open_positions": len(pf["positions"])})

    # 4) kill switch: 15% below the peak -> everything to cash, wait for review
    dd = (st["peak_equity"] - eq) / st["peak_equity"] * 100
    if dd >= m["max_drawdown_pct"] and not pf["blocks"].get("month_halted"):
        pf["blocks"]["month_halted"] = True
        msg = f"KILL SWITCH: account {dd:.1f}% below its peak — selling everything, full stop until reviewed"
        P.log(pf, "LIMIT", msg, c_t)
        msgs.append(msg)
        pf["pending"] = [{"side": "sell", "symbol": p["symbol"], "reason": "kill switch"} for p in pf["positions"]]
        return msgs

    # 5) monthly decision (last trading day of the month, or first run)
    nxt = next_trading_day(day, cal)
    if (nxt.month != day.month or st.get("needs_initial")) and not pf["blocks"].get("month_halted") \
            and not st.get("paused"):
        st["needs_initial"] = False
        n = nifty.loc[:day].dropna()
        risk_on = len(n) >= m["market_sma"] and n.iloc[-1] > n.iloc[-m["market_sma"]:].mean()
        ranked = rank(closes, day, m)
        st["ranking"] = ranked.head(15).to_dict("records") if not ranked.empty else []
        st["ranking_date"] = day.isoformat()
        st["risk_on"] = bool(risk_on)
        nifty_txt = f"Nifty {n.iloc[-1]:.0f} vs 200-day avg {n.iloc[-m['market_sma']:].mean():.0f}" if len(n) >= m["market_sma"] else "Nifty history short"
        picks, excluded = [], {}
        if risk_on and not ranked.empty:
            pool = ranked[ranked["above_200d"]]["symbol"].tolist()
            if use_ai:
                excluded = ai_review(pool[: m["top_n"] + 3], rules)
            picks = [s for s in pool if s not in excluded][: m["top_n"]]
        st["picks"] = picks
        st["excluded"] = excluded
        held = {p["symbol"] for p in pf["positions"]}
        for p in pf["positions"]:
            if p["symbol"] not in picks:
                pf["pending"].append({"side": "sell", "symbol": p["symbol"],
                                      "reason": "monthly rebalance" if risk_on else "market below 200-day avg → cash"})
        sell_value = sum(p["last_price"] * p["qty"] for p in pf["positions"] if p["symbol"] not in picks)
        alloc = (pf["cash"] + sell_value + sum(p["last_price"] * p["qty"] for p in pf["positions"] if p["symbol"] in picks)) \
            * 0.97 / m["top_n"]
        for s in picks:
            if s not in held:
                r = next(x for x in st["ranking"] if x["symbol"] == s) if any(x["symbol"] == s for x in st["ranking"]) else {}
                pf["pending"].append({"side": "buy", "symbol": s, "amount": round(alloc, 2),
                                      "reason": f"top-{m['top_n']} momentum: {r.get('return_12m_pct', '?')}% in 12 months"})
        for s, why in excluded.items():
            P.log(pf, "AI", f"excluded {s} from picks — {why}", c_t)
        decision = ("MARKET BELOW 200-DAY AVG → holding cash. " if not risk_on else "") + \
                   f"Rebalance decided on {day}: picks {', '.join(picks) or 'none'} ({nifty_txt}). Orders fill at {nxt} open."
        P.log(pf, "PLAN", decision, c_t)
        msgs.append(decision)
    st["next_rebalance"] = _month_last_trading_day(nxt, cal).isoformat()
    return msgs


def _month_last_trading_day(d, cal):
    x = d
    while next_trading_day(x, cal).month == d.month:
        x = next_trading_day(x, cal)
    return x


def run(pf, rules, cal, provider, now=None, symbols=None, use_ai=True) -> list[str]:
    from .universe import fetch_nifty50

    now = now or now_ist()
    st = read_json(STATE_PATH, {"needs_initial": True})
    target = last_completed_day(now, cal)
    syms = symbols or fetch_nifty50()
    daily = provider.daily(syms, period="2y")
    nd = provider.index_daily({"Nifty 50": "^NSEI"}, period="2y").get("Nifty 50")
    if not daily or nd is None:
        return ["no market data — will catch up next run"]
    opens = pd.DataFrame({s: df["Open"] for s, df in daily.items()}).sort_index()
    highs = pd.DataFrame({s: df["High"] for s, df in daily.items()}).reindex(opens.index)
    lows = pd.DataFrame({s: df["Low"] for s, df in daily.items()}).reindex(opens.index)
    closes = pd.DataFrame({s: df["Close"] for s, df in daily.items()}).reindex(opens.index)
    nifty = nd["Close"].copy()
    nifty.index = pd.to_datetime(nifty.index).date
    days = [d for d in closes.index if d <= target]
    last = st.get("last_day")
    todo = [d for d in days if last is None or d > dt.date.fromisoformat(last)]
    if last is None:
        todo = todo[-1:]            # first run: start from the latest finished day, no back-filling
    msgs = []
    for d in todo:
        msgs += process_day(pf, st, rules, cal, d, opens, highs, lows, closes, nifty, use_ai=use_ai)
        st["last_day"] = d.isoformat()
    write_json(STATE_PATH, st)
    return msgs or [f"nothing new (last processed {st.get('last_day')})"]
