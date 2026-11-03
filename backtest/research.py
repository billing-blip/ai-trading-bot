"""Research round: test alternative strategies honestly.

Every variant is judged on two separate periods:
  * IN-SAMPLE      2022-2024  (where ideas were chosen)
  * OUT-OF-SAMPLE  2025-2026  (data the ideas never "saw")
A variant only passes if it is profitable in BOTH, so we don't fool ourselves.

  python -m backtest.research
"""
from __future__ import annotations

import copy
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.run_backtest import RESULTS, _ts, swing_backtest  # noqa: E402
from bot import portfolio as P  # noqa: E402
from bot.config import load_rules, write_json  # noqa: E402
from bot.report import stats  # noqa: E402
from bot.risk import equity  # noqa: E402
from bot.strategies import _setup, breakout, trend_pullback  # noqa: E402
from bot.timeutil import IST  # noqa: E402

SPLIT = dt.date(2025, 1, 1)


# ------------------------------------------------------------ extra strategy

def donchian50(symbol, df, rules, n=50):
    """Classic trend-following entry: close at a new 50-day high, in an uptrend."""
    if len(df) < n + 2:
        return None
    last = df.iloc[-1]
    prior_high = df["High"].iloc[-n - 1:-1].max()
    if pd.isna(last["atr"]) or pd.isna(last["sma_trend"]):
        return None
    if last["Close"] > prior_high and last["Close"] > last["sma_trend"]:
        entry = last["High"] * 1.001
        return _setup(symbol, "donchian50", "swing", entry, entry - 2 * last["atr"],
                      rules["risk"]["min_reward_to_risk"], "New 50-day high in an uptrend")
    return None


# ------------------------------------------------------------ momentum rotation

def momentum_backtest(daily: dict[str, pd.DataFrame], nifty: pd.Series | None, rules: dict,
                      lookback: int = 126, top_n: int = 5, market_filter: bool = True,
                      stop_pct: float | None = None) -> dict:
    """Monthly: hold the top_n strongest stocks (by `lookback`-day return), each above its
    200-day average. Market filter: hold cash when Nifty is below its 200-day average."""
    closes = pd.DataFrame({s: df["Close"] for s, df in daily.items()}).sort_index()
    opens = pd.DataFrame({s: df["Open"] for s, df in daily.items()}).reindex(closes.index)
    lows = pd.DataFrame({s: df["Low"] for s, df in daily.items()}).reindex(closes.index)
    sma200 = closes.rolling(200, min_periods=200).mean()
    n_sma = nifty.rolling(200).mean() if nifty is not None else None
    dates = list(closes.index)
    pf = P.new_portfolio(rules)
    pf["events"] = []
    name = f"momentum_{lookback}d_top{top_n}"
    last_month = None

    for i, day in enumerate(dates):
        if i < 252:
            continue
        open_t, close_t = _ts(day, 9, 15), _ts(day)
        # stop-losses (optional)
        if stop_pct:
            for p in list(pf["positions"]):
                lo, op = lows.at[day, p["symbol"]], opens.at[day, p["symbol"]]
                if pd.notna(op) and op <= p["stop"]:
                    P.close_position(pf, p, float(op), close_t, "stop-loss (gapped through)", rules)
                elif pd.notna(lo) and lo <= p["stop"]:
                    P.close_position(pf, p, p["stop"], close_t, "stop-loss", rules)
        # monthly rebalance on the first trading day of the month, using yesterday's close
        if (day.year, day.month) != last_month:
            last_month = (day.year, day.month)
            prev = dates[i - 1]
            risk_on = True
            if market_filter and n_sma is not None:
                nv, ns = nifty.get(prev), n_sma.get(prev)
                risk_on = bool(pd.notna(nv) and pd.notna(ns) and nv > ns)
            picks = []
            if risk_on:
                mom = closes.loc[prev] / closes.iloc[i - 1 - lookback] - 1
                ok = closes.loc[prev] > sma200.loc[prev]
                picks = list(mom[ok].dropna().sort_values(ascending=False).index[:top_n])
            for p in list(pf["positions"]):
                if p["symbol"] not in picks:
                    px = opens.at[day, p["symbol"]]
                    P.close_position(pf, p, float(px if pd.notna(px) else p["last_price"]), open_t, "monthly rebalance", rules)
            held = {p["symbol"] for p in pf["positions"]}
            new = [s for s in picks if s not in held]
            if new:
                slot = equity(pf, rules) * 0.97 / top_n
                for s in new:
                    px = opens.at[day, s]
                    if pd.isna(px) or px <= 0:
                        continue
                    qty = int(min(slot, pf["cash"] * 0.99) // (px * 1.001))
                    if qty < 1:
                        continue
                    stop = round(px * (1 - stop_pct), 2) if stop_pct else 0.01
                    P.open_position(pf, {"id": P.new_id(), "symbol": s, "strategy": name, "type": "swing",
                                         "qty": qty, "stop": stop, "target": px * 100}, float(px), open_t, rules)
        for p in pf["positions"]:
            c = closes.at[day, p["symbol"]]
            if pd.notna(c):
                p["last_price"] = float(c)
        pf["equity_history"].append({"date": day.isoformat(), "equity": equity(pf, rules)})
    for p in list(pf["positions"]):
        P.close_position(pf, p, p["last_price"], _ts(dates[-1]), "end of backtest", rules)
    return {"trades": pf["closed"], "equity": pf["equity_history"]}


# ------------------------------------------------------------ scoring

def period_metrics(res: dict, start: dt.date | None, end: dt.date | None, cap: float) -> dict:
    def inside(d):
        d = dt.date.fromisoformat(d[:10])
        return (start is None or d >= start) and (end is None or d < end)

    # trades are assigned to the period they were OPENED in (a 2024 winner sold in 2025 is 2024's profit)
    trades = [t for t in res["trades"] if inside(t["entry_time"])]
    s = stats(trades, cap)
    eq = [e for e in res["equity"] if inside(e["date"])]
    ret, dd = None, 0.0
    if len(eq) > 1:
        ret = round((eq[-1]["equity"] / eq[0]["equity"] - 1) * 100, 1)
        peak = eq[0]["equity"]
        for e in eq:
            peak = max(peak, e["equity"])
            dd = max(dd, (peak - e["equity"]) / peak * 100)
    s["return_pct"] = ret
    s["equity_dd_pct"] = round(dd, 1)
    if len(eq) > 1:                       # profit = change in account value during the period
        s["net_pnl"] = round(eq[-1]["equity"] - eq[0]["equity"], 2)
    return s


def nifty_return(nifty: pd.Series, start, end) -> float | None:
    s = nifty[[(start is None or d >= start) and (end is None or d < end) for d in nifty.index]]
    return round((s.iloc[-1] / s.iloc[0] - 1) * 100, 1) if len(s) > 1 else None


def verdict(is_: dict, oos: dict) -> str:
    if is_["trades"] < 15 or oos["trades"] < 8:
        return "NOT ENOUGH TRADES"
    good = lambda m: m["net_pnl"] > 0 and (m["profit_factor"] or 0) >= 1.2  # noqa: E731
    if good(is_) and good(oos) and oos["equity_dd_pct"] < 15:
        return "PASS"
    if is_["net_pnl"] > 0 and oos["net_pnl"] > 0:
        return "WEAK"
    return "FAIL"


def variants(rules: dict):
    def mod(**kw):
        r = copy.deepcopy(rules)
        for path, val in kw.items():
            sec, key = path.split("__")
            r[sec][key] = val
        return r

    no_be = dict(trade_rules__move_stop_to_breakeven_at_r=99)
    return [
        ("A. Current menu, 3R target, no breakeven, 20-day hold, Nifty>200d",
         lambda d, n: swing_backtest(d, mod(**no_be, risk__min_reward_to_risk=3, trade_rules__swing_max_hold_days=20),
                                     n, trend_filter=True, filter_sma=200)),
        ("B. Breakout only, trailing stop 3xATR, up to 60 days, Nifty>200d",
         lambda d, n: swing_backtest(d, mod(**no_be, risk__min_reward_to_risk=50, trade_rules__swing_max_hold_days=60),
                                     n, trend_filter=True, filter_sma=200, strategies=[breakout], trail_atr=3)),
        ("C. Trend following: 50-day high, trailing 3xATR, up to 120 days, Nifty>200d",
         lambda d, n: swing_backtest(d, mod(**no_be, risk__min_reward_to_risk=50, trade_rules__swing_max_hold_days=120),
                                     n, trend_filter=True, filter_sma=200, strategies=[donchian50], trail_atr=3)),
        ("D. Pullback only, 3R target, no breakeven, Nifty>200d",
         lambda d, n: swing_backtest(d, mod(**no_be, risk__min_reward_to_risk=3, trade_rules__swing_max_hold_days=20),
                                     n, trend_filter=True, filter_sma=200, strategies=[trend_pullback])),
        ("E. Momentum rotation: top 5 by 6-month return, monthly, cash when Nifty<200d",
         lambda d, n: momentum_backtest(d, n, rules, lookback=126, top_n=5, market_filter=True)),
        ("F. Momentum rotation: top 5 by 12-month return, monthly, cash when Nifty<200d",
         lambda d, n: momentum_backtest(d, n, rules, lookback=252, top_n=5, market_filter=True)),
        ("G. Momentum rotation: top 5 by 6-month return, monthly, no market filter",
         lambda d, n: momentum_backtest(d, n, rules, lookback=126, top_n=5, market_filter=False)),
        ("I. Momentum F + 12% stop-loss per stock",
         lambda d, n: momentum_backtest(d, n, rules, lookback=252, top_n=5, market_filter=True, stop_pct=0.12)),
        ("H. Momentum E + 12% stop-loss per stock",
         lambda d, n: momentum_backtest(d, n, rules, lookback=126, top_n=5, market_filter=True, stop_pct=0.12)),
    ]


def run_all(daily, nifty, rules) -> dict:
    cap = rules["capital"]["starting"]
    out = {}
    for name, fn in variants(rules):
        print("running", name, flush=True)
        res = fn(daily, nifty)
        is_ = period_metrics(res, None, SPLIT, cap)
        oos = period_metrics(res, SPLIT, None, cap)
        out[name] = {"in_sample": is_, "out_of_sample": oos, "verdict": verdict(is_, oos),
                     "equity": res["equity"][::5], "trades": len(res["trades"])}
    return out


def write_report(out: dict, nifty: pd.Series | None) -> str:
    L = ["# Research round — alternative strategies", "",
         f"_Generated {dt.datetime.now(IST):%Y-%m-%d %H:%M} IST · ₹1,00,000 paper capital · realistic charges_", "",
         "**In-sample** = up to Dec 2024 (ideas chosen here). **Out-of-sample** = Jan 2025 onward "
         "(unseen data — the real test). PASS needs profit factor ≥ 1.2 in BOTH and a drawdown under 15%.", ""]
    if nifty is not None:
        L.append(f"Benchmark — just holding Nifty 50: in-sample {nifty_return(nifty, None, SPLIT)}%, "
                 f"out-of-sample {nifty_return(nifty, SPLIT, None)}%.")
        L.append("")
    L += ["| Variant | Verdict | Trades | In-sample net | In-sample PF | Out-of-sample net | Out-of-sample PF | OOS return | OOS max drop |",
          "|---|---|---|---|---|---|---|---|---|"]
    order = {"PASS": 0, "WEAK": 1, "NOT ENOUGH TRADES": 2, "FAIL": 3}
    for name, r in sorted(out.items(), key=lambda kv: (order[kv[1]["verdict"]], -kv[1]["out_of_sample"]["net_pnl"])):
        i, o = r["in_sample"], r["out_of_sample"]
        L.append(f"| {name} | **{r['verdict']}** | {r['trades']} | ₹{i['net_pnl']:,.0f} | {i['profit_factor']} | "
                 f"₹{o['net_pnl']:,.0f} | {o['profit_factor']} | {o['return_pct']}% | {o['equity_dd_pct']}% |")
    L += ["", "Uses today's Nifty 50 list for the whole period (survivorship bias): real results would be "
          "somewhat worse, especially for momentum. Momentum variants hold ~₹20k per stock with no per-trade "
          "₹500 cap, so they would need a rulebook change before paper trading."]
    return "\n".join(L)


def main():
    from bot.data import YahooProvider
    from bot.universe import fetch_nifty50

    rules = load_rules()
    prov = YahooProvider()
    syms = fetch_nifty50()
    daily = prov.history(syms, period="5y")
    ndf = prov.index_daily({"Nifty 50": "^NSEI"}, period="5y").get("Nifty 50")
    nifty = None
    if ndf is not None:
        nifty = ndf["Close"].copy()
        nifty.index = pd.to_datetime(nifty.index).date
    out = run_all(daily, nifty, rules)
    RESULTS.mkdir(parents=True, exist_ok=True)
    text = write_report(out, nifty)
    (RESULTS / "research.md").write_text(text)
    bench = None
    if nifty is not None:
        bench = [{"date": d.isoformat(), "value": round(float(v) / float(nifty.iloc[0]) * rules["capital"]["starting"], 2)}
                 for d, v in list(nifty.items())[::5]]
    write_json(RESULTS / "research.json", {"variants": out, "nifty_benchmark": bench, "split": SPLIT.isoformat()})
    print(text)


if __name__ == "__main__":
    main()
