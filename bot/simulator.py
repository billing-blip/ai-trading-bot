"""Paper-trade simulator.

Walks through 5-minute price candles in time order and decides, exactly as a broker
would have, whether pending entries filled and whether stops/targets were hit.
Because it works from candles, a late check (e.g. GitHub ran 20 min late) still records
every fill at the right price — nothing is missed.

Conservative assumptions (so paper results are never flattering):
  * if a candle touches both stop and target, we assume the STOP was hit first
  * if a candle opens beyond the stop (gap), we exit at the open, not at the stop
  * buys fill at max(entry, candle open); slippage is added on every fill
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from . import portfolio as P
from .risk import apply_loss_limits, blocked_reason, refit_qty
from .timeutil import at, parse_hhmm

CANDLE = pd.Timedelta(minutes=5)


def _complete(df: pd.DataFrame, now: dt.datetime) -> pd.DataFrame:
    return df[df.index + CANDLE <= now]


def nifty_gap_check(pf: dict, rules: dict, day: dt.date, nifty_prev_close: float | None,
                    nifty_open: float | None) -> str | None:
    if not nifty_prev_close or not nifty_open:
        return None
    gap = (nifty_open - nifty_prev_close) / nifty_prev_close * 100
    lim = rules["gap_check"]["max_nifty_gap_pct"]
    if abs(gap) > lim and pf["blocks"].get("no_trade_day") != day.isoformat():
        pf["blocks"]["no_trade_day"] = day.isoformat()
        pf["blocks"]["no_trade_reason"] = f"Nifty opened {gap:+.2f}% (limit ±{lim}%) — unusual day"
        for o in list(pf["pending"]):
            P.log(pf, "CANCEL", f"{o['symbol']} entry cancelled — Nifty gap {gap:+.2f}%")
        pf["pending"] = []
        return pf["blocks"]["no_trade_reason"]
    return None


def cancel_all_pending(pf: dict, reason: str, when: dt.datetime) -> None:
    for o in pf["pending"]:
        P.log(pf, "CANCEL", f"{o['symbol']} entry cancelled — {reason}", when)
    pf["pending"] = []


def run(pf: dict, rules: dict, control: dict, day: dt.date, candles: dict[str, pd.DataFrame],
        now: dt.datetime) -> list[str]:
    """Process all complete candles up to `now`. Returns human-readable alerts."""
    tr = rules["trade_rules"]
    alerts: list[str] = []
    gap_lim = rules["gap_check"]["max_stock_gap_pct"]
    sq_off = at(day, tr["intraday_square_off"])
    swing_lo, swing_hi = (at(day, x) for x in tr["swing_entry_window"])
    intra_lo, intra_hi = at(day, tr["intraday_no_entry_before"]), at(day, tr["intraday_no_entry_after"])
    first_candle = at(day, "09:15")

    data = {s: _complete(df, now) for s, df in candles.items()}
    stamps = sorted({ts for df in data.values() for ts in df.index})

    for ts in stamps:
        # ----- pending entries -----
        for o in list(pf["pending"]):
            df = data.get(o["symbol"])
            if df is None or ts not in df.index:
                continue
            last = pd.Timestamp(o.get("last_processed", "1970-01-01T00:00:00+00:00"))
            if ts <= last:
                continue
            o["last_processed"] = ts.isoformat()
            bar = df.loc[ts]
            if ts == first_candle and bar["Open"] > o["entry"] * (1 + gap_lim / 100):
                pf["pending"].remove(o)
                P.log(pf, "CANCEL", f"{o['symbol']} opened {((bar['Open'] / o['entry']) - 1) * 100:+.1f}% above entry — gap rule", ts)
                continue
            if o.get("active_from") and ts < pd.Timestamp(o["active_from"]):
                continue
            lo, hi = (swing_lo, swing_hi) if o["type"] == "swing" else (intra_lo, intra_hi)
            if ts < lo:
                continue
            if ts > hi:
                pf["pending"].remove(o)
                P.log(pf, "CANCEL", f"{o['symbol']} entry window closed without trigger", ts)
                continue
            if bar["High"] >= o["entry"]:
                why = blocked_reason(pf, control, day)
                if why:
                    pf["pending"].remove(o)
                    P.log(pf, "CANCEL", f"{o['symbol']} not entered — {why}", ts)
                    continue
                pf["pending"].remove(o)
                fill = max(o["entry"], float(bar["Open"]))
                qty = refit_qty(o, fill, rules)
                if qty < 1:
                    P.log(pf, "CANCEL", f"{o['symbol']} jumped to {fill:.2f}, risk too big at any size — skipped", ts)
                    continue
                if qty != o["qty"]:
                    P.log(pf, "RESIZE", f"{o['symbol']} opened above entry; qty {o['qty']} -> {qty} to keep risk within limit", ts)
                    o = {**o, "qty": qty}
                pos = P.open_position(pf, o, fill, ts, rules)
                pos["last_processed"] = (ts - pd.Timedelta(seconds=1)).isoformat()  # evaluate this bar for stop
                alerts.append(f"Entered {pos['symbol']} @ {pos['entry_price']}")

        # ----- open positions -----
        for pos in list(pf["positions"]):
            df = data.get(pos["symbol"])
            if df is None or ts not in df.index:
                continue
            if ts <= pd.Timestamp(pos["last_processed"]):
                continue
            bar = df.loc[ts]
            pos["last_processed"] = ts.isoformat()
            pos["last_price"] = round(float(bar["Close"]), 2)
            exit_px, why = None, None
            if pos["type"] == "intraday" and ts >= sq_off:
                exit_px, why = float(bar["Open"]), "intraday square-off 15:15"
            elif bar["Open"] <= pos["stop"]:
                exit_px, why = float(bar["Open"]), "stop-loss (gapped through)"
            elif bar["Low"] <= pos["stop"]:
                exit_px, why = pos["stop"], ("breakeven stop" if pos["stop"] >= pos["entry_price"] else "stop-loss")
            elif bar["High"] >= pos["target"]:
                exit_px, why = max(pos["target"], float(bar["Open"])), "target hit"
            if exit_px is not None:
                t = P.close_position(pf, pos, exit_px, ts, why, rules)
                alerts.append(f"Exited {t['symbol']}: {why}, net {t['net_pnl']:+.0f}")
                for msg in apply_loss_limits(pf, rules, day):
                    alerts.append(msg)
                    P.log(pf, "LIMIT", msg, ts)
                    cancel_all_pending(pf, msg, ts)
                continue
            be = pos["entry_price"] + rules["trade_rules"]["move_stop_to_breakeven_at_r"] * pos["initial_risk_ps"]
            if bar["High"] >= be and pos["stop"] < pos["entry_price"]:
                pos["stop"] = pos["entry_price"]
                P.log(pf, "STOP", f"{pos['symbol']} stop moved to breakeven {pos['stop']}", ts)

    return alerts


def end_of_day(pf: dict, rules: dict, day: dt.date, closes: dict[str, float], now: dt.datetime) -> list[str]:
    """After the close: cancel unfilled orders, close leftover intraday, age swing trades."""
    alerts = []
    cancel_all_pending(pf, "day ended (orders are valid for one day)", now)
    max_days = rules["trade_rules"]["swing_max_hold_days"]
    for pos in list(pf["positions"]):
        px = closes.get(pos["symbol"], pos["last_price"])
        if pos["type"] == "intraday":
            t = P.close_position(pf, pos, px, now, "intraday closed at end of day", rules)
            alerts.append(f"Closed leftover intraday {t['symbol']}")
            continue
        pos["days_held"] = pos.get("days_held", 0) + 1   # entry day counts as day 1
        pos["last_price"] = px
        if pos["days_held"] >= max_days:
            t = P.close_position(pf, pos, px, now, f"time exit after {max_days} trading days", rules)
            alerts.append(f"Time exit {t['symbol']}, net {t['net_pnl']:+.0f}")
    return alerts
