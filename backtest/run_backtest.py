"""Backtest: how would the strategy menu have done in the past, with the SAME rules,
sizing, charges and loss limits the live paper bot uses?

  python -m backtest.run_backtest            # 5 years daily (swing) + 60 days 5-min (ORB)

The AI's daily judgement can't be replayed historically, so two simple stand-ins are
tested side by side:
  * "no filter"     — take every valid setup (worst case: no judgement at all)
  * "trend filter"  — only trade when Nifty is above its 50-day average (a crude proxy for
                      the AI saying "trending_up / sideways" and skipping "falling")

Known limitation: uses TODAY's Nifty 50 list for the whole period (survivorship bias —
results look a bit better than reality). Treat results as a sanity check, not a promise.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import portfolio as P  # noqa: E402
from bot import simulator  # noqa: E402
from bot.config import load_rules, write_json  # noqa: E402
from bot.indicators import add_all  # noqa: E402
from bot.report import by_strategy, stats  # noqa: E402
from bot.risk import (apply_loss_limits, blocked_reason, bucket_available, can_add,  # noqa: E402
                      equity, refit_qty, size_trade)
from bot.strategies import SWING_STRATEGIES, orb_eligible, orb_setup  # noqa: E402
from bot.timeutil import IST  # noqa: E402

RESULTS = ROOT / "backtest" / "results"
STRAT_ORDER = {"breakout": 0, "trend_pullback": 1}


def _ts(day: dt.date, hh=15, mm=30) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST)


def swing_backtest(daily: dict[str, pd.DataFrame], rules: dict, nifty: pd.Series | None = None,
                   trend_filter: bool = False, warmup: int = 60) -> dict:
    ind = {s: add_all(df, rules) for s, df in daily.items() if len(df) > warmup + 10}
    dates = sorted({d for df in ind.values() for d in df.index})
    pos_in = {s: {d: i for i, d in enumerate(df.index)} for s, df in ind.items()}
    nifty_ok = None
    if nifty is not None:
        sma50 = nifty.rolling(50).mean()
        nifty_ok = (nifty > sma50).to_dict()

    pf = P.new_portfolio(rules)
    pf["events"] = []
    halts, month = 0, None
    gap_lim = rules["gap_check"]["max_stock_gap_pct"]
    max_days = rules["trade_rules"]["swing_max_hold_days"]
    be_r = rules["trade_rules"]["move_stop_to_breakeven_at_r"]

    for di, day in enumerate(dates[warmup:], start=warmup):
        if month != (day.year, day.month):          # monthly halt = "review"; resume next month
            month = (day.year, day.month)
            if pf["blocks"].get("month_halted"):
                pf["blocks"]["month_halted"] = False
        open_t = _ts(day, 9, 30)
        close_t = _ts(day)

        # 1) manage open positions on today's bar
        for p in list(pf["positions"]):
            i = pos_in[p["symbol"]].get(day)
            if i is None:
                continue
            bar = ind[p["symbol"]].iloc[i]
            exit_px = why = None
            if bar["Open"] <= p["stop"]:
                exit_px, why = bar["Open"], "stop-loss (gapped through)"
            elif bar["Low"] <= p["stop"]:
                exit_px, why = p["stop"], "stop-loss"
            elif bar["High"] >= p["target"]:
                exit_px, why = max(p["target"], bar["Open"]), "target hit"
            if exit_px is None:
                if bar["High"] >= p["entry_price"] + be_r * p["initial_risk_ps"]:
                    p["stop"] = max(p["stop"], p["entry_price"])
                p["days_held"] += 1
                p["last_price"] = float(bar["Close"])
                if p["days_held"] >= max_days:
                    exit_px, why = bar["Close"], "time exit"
            if exit_px is not None:
                P.close_position(pf, p, float(exit_px), close_t, why, rules)

        # 2) fill yesterday's pending orders
        for o in list(pf["pending"]):
            pf["pending"].remove(o)
            i = pos_in[o["symbol"]].get(day)
            if i is None:
                continue
            bar = ind[o["symbol"]].iloc[i]
            if bar["Open"] > o["entry"] * (1 + gap_lim / 100) or bar["High"] < o["entry"]:
                continue
            fill = max(o["entry"], float(bar["Open"]))
            qty = refit_qty(o, fill, rules)
            if qty < 1:
                continue
            p = P.open_position(pf, {**o, "qty": qty}, fill, open_t, rules)
            p["days_held"] = 1
            if bar["Low"] <= p["stop"]:                 # same-day stop: assume the worst
                P.close_position(pf, p, p["stop"], close_t, "stop-loss (same day)", rules)
            elif bar["High"] >= p["target"] and bar["Close"] >= p["target"]:
                P.close_position(pf, p, p["target"], close_t, "target hit (same day)", rules)
            else:
                p["last_price"] = float(bar["Close"])

        # 3) end of day: equity + loss limits
        pf["equity_history"].append({"date": day.isoformat(), "equity": equity(pf, rules)})
        before = pf["blocks"].get("month_halted")
        apply_loss_limits(pf, rules, day)
        if pf["blocks"].get("month_halted") and not before:
            halts += 1

        # 4) tomorrow's orders from today's close
        if di + 1 >= len(dates):
            break
        nxt = dates[di + 1]
        if blocked_reason(pf, {}, nxt):
            continue
        if trend_filter and nifty_ok is not None and not nifty_ok.get(day, True):
            continue
        setups = []
        for s, df in ind.items():
            i = pos_in[s].get(day)
            if i is None or i < warmup:
                continue
            hist = df.iloc[: i + 1]
            for strat in SWING_STRATEGIES:
                st = strat(s, hist, rules)
                if st:
                    setups.append(st)
        setups.sort(key=lambda x: (STRAT_ORDER.get(x["strategy"], 9), x["risk_per_share"] / x["entry"]))
        for st in setups:
            sz = size_trade(st, rules, bucket_available(pf, rules, "swing"))
            if not sz:
                continue
            o = {**st, **sz, "id": P.new_id(), "plan_date": nxt.isoformat()}
            if can_add(pf, rules, o, nxt) is None:
                pf["pending"].append(o)

    # close anything still open at the last price
    for p in list(pf["positions"]):
        P.close_position(pf, p, p["last_price"], _ts(dates[-1]), "end of backtest", rules)
    return {"trades": pf["closed"], "equity": pf["equity_history"], "monthly_halts": halts}


def orb_backtest(intraday: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame], rules: dict,
                 nifty: pd.Series | None) -> dict:
    ind = {s: add_all(df, rules) for s, df in daily.items()}
    days = sorted({ts.date() for df in intraday.values() for ts in df.index})
    pf = P.new_portfolio(rules)
    pf["events"] = []
    for day in days:
        if nifty is not None:
            past = nifty[[d.date() < day if hasattr(d, "date") else d < day for d in nifty.index]]
            if len(past) < 50 or not (past.iloc[-1] > past.rolling(20).mean().iloc[-1] > past.rolling(50).mean().iloc[-1]):
                continue                                 # proxy for "trending_up" regime
        picks = []
        for s, df in ind.items():
            hist = df[[d < day for d in df.index]]
            if len(hist) < 60 or s not in intraday:
                continue
            if orb_eligible(s, hist, rules):
                mom = hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1
                picks.append((mom, s))
        picks = [s for _, s in sorted(picks, reverse=True)[: rules["risk"]["max_intraday_trades_per_day"]]]
        candles = {}
        for s in picks:
            c = intraday[s][intraday[s].index.date == day]
            st = orb_setup(s, c, day, rules)
            if not st:
                continue
            sz = size_trade(st, rules, bucket_available(pf, rules, "intraday"))
            if not sz:
                continue
            o = {**st, **sz, "id": P.new_id(),
                 "last_processed": (pd.Timestamp(st["active_from"]) - pd.Timedelta(seconds=1)).isoformat()}
            if can_add(pf, rules, o, day) is None:
                pf["pending"].append(o)
                candles[s] = c
        if candles:
            simulator.run(pf, rules, {}, day, candles, _ts(day, 15, 31))
        simulator.end_of_day(pf, rules, day, {}, _ts(day, 15, 31))
        pf["equity_history"].append({"date": day.isoformat(), "equity": equity(pf, rules)})
        pf["blocks"] = {}
    return {"trades": pf["closed"], "equity": pf["equity_history"]}


def by_year(trades, cap):
    out = {}
    for y in sorted({t["exit_time"][:4] for t in trades}):
        out[y] = stats([t for t in trades if t["exit_time"][:4] == y], cap)
    return out


def verdict(s: dict) -> str:
    if s["trades"] < 30:
        return "NOT ENOUGH TRADES to judge"
    if s["net_pnl"] > 0 and (s["profit_factor"] or 0) >= 1.2:
        return "PASS — keep for paper trading"
    if s["net_pnl"] > 0:
        return "WEAK — profitable but thin edge"
    return "FAIL — drop or rework"


def write_summary(res: dict, rules: dict) -> str:
    cap = rules["capital"]["starting"]
    L = ["# Backtest results", "", f"_Generated {dt.datetime.now(IST):%Y-%m-%d %H:%M} IST_", "",
         "Same rules as the live bot: ₹500 max loss per trade (incl. charges & slippage), "
         "max 4 swing trades, ₹2,000 total open risk, daily/weekly/monthly loss limits, "
         "realistic brokerage + taxes. Uses today's Nifty 50 list (survivorship bias → real results "
         "would be somewhat worse).", ""]
    for name, r in res.items():
        s = stats(r["trades"], cap)
        L += [f"## {name}", "",
              f"**Verdict: {verdict(s)}**", "",
              "| Trades | Win rate | Net P&L | Profit factor | Avg win | Avg loss | Max drawdown | Charges |",
              "|---|---|---|---|---|---|---|---|",
              f"| {s['trades']} | {s['win_rate']}% | ₹{s['net_pnl']:,.0f} | {s['profit_factor']} | "
              f"₹{s['avg_win']} | ₹{s['avg_loss']} | {s['max_drawdown_pct']}% | ₹{s['charges']:,.0f} |", ""]
        bs = by_strategy(r["trades"], cap)
        if len(bs) > 1:
            L += ["By strategy:", "", "| Strategy | Trades | Win rate | Net | PF | Verdict |", "|---|---|---|---|---|---|"]
            for k, v in bs.items():
                L.append(f"| {k} | {v['trades']} | {v['win_rate']}% | ₹{v['net_pnl']:,.0f} | {v['profit_factor']} | {verdict(v)} |")
            L.append("")
        yr = by_year(r["trades"], cap)
        if len(yr) > 1:
            L += ["By year:", "", "| Year | Trades | Net | PF | Max DD |", "|---|---|---|---|---|"]
            for y, v in yr.items():
                L.append(f"| {y} | {v['trades']} | ₹{v['net_pnl']:,.0f} | {v['profit_factor']} | {v['max_drawdown_pct']}% |")
            L.append("")
        if r.get("monthly_halts") is not None:
            L += [f"Monthly loss-limit full stops triggered: {r['monthly_halts']}", ""]
    return "\n".join(L)


def main():
    from bot.data import YahooProvider
    from bot.universe import fetch_nifty50

    rules = load_rules()
    prov = YahooProvider()
    syms = fetch_nifty50()
    print(f"Downloading 5y daily data for {len(syms)} stocks...")
    daily = prov.history(syms, period="5y")
    nifty_df = prov.index_daily({"Nifty 50": "^NSEI"}, period="5y").get("Nifty 50")
    nifty = None
    if nifty_df is not None:
        nifty = nifty_df["Close"].copy()
        nifty.index = pd.to_datetime(nifty.index).date
    print("Running swing backtests...")
    res = {
        "Swing — no filter": swing_backtest(daily, rules, nifty, trend_filter=False),
        "Swing — trend filter (AI proxy)": swing_backtest(daily, rules, nifty, trend_filter=True),
    }
    print("Downloading 60 days of 5-min data for ORB...")
    intra = prov.intraday_history(syms, period="60d", interval="5m")
    res["Intraday ORB — last ~60 days (trend days only)"] = orb_backtest(intra, daily, rules, nifty)
    RESULTS.mkdir(parents=True, exist_ok=True)
    text = write_summary(res, rules)
    (RESULTS / "summary.md").write_text(text)
    write_json(RESULTS / "results.json", {k: {"stats": stats(v["trades"], rules["capital"]["starting"]),
                                              "by_strategy": by_strategy(v["trades"], rules["capital"]["starting"]),
                                              "equity": v["equity"], "trades": v["trades"]} for k, v in res.items()})
    print(text)


if __name__ == "__main__":
    main()
