import datetime as dt

import numpy as np
import pandas as pd

from bot import momentum
from bot import portfolio as P
from bot.config import load_calendar, load_rules
from bot.data import DataProvider
from bot.timeutil import at

RULES, CAL = load_rules(), load_calendar()
DAYS = [d.date() for d in pd.bdate_range(end="2026-11-30", periods=320) if d.date() not in CAL["holidays"]]


def frame(drift, seed):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, len(DAYS))))
    return pd.DataFrame({"Open": c * 0.999, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, index=DAYS)


class Fake(DataProvider):
    def __init__(self, upto):
        self.upto = upto

    def daily(self, symbols, period="2y"):
        drifts = {"UP1": 0.003, "UP2": 0.0025, "UP3": 0.002, "UP4": 0.0018, "UP5": 0.0015, "UP6": 0.001, "DOWN": -0.002}
        return {s: frame(drifts[s], i)[lambda df: df.index <= self.upto] for i, s in enumerate(symbols)}

    def index_daily(self, tickers, period="2y"):
        return {"Nifty 50": frame(0.001, 99)[lambda df: df.index <= self.upto]}


SYMS = ["UP1", "UP2", "UP3", "UP4", "UP5", "UP6", "DOWN"]


def test_momentum_first_run_then_fill_then_month_end(tmp_path):
    pf = P.new_portfolio(RULES)
    d0 = dt.date(2026, 11, 4)
    msgs = momentum.run(pf, RULES, CAL, Fake(d0), now=at(d0, "16:30"), symbols=SYMS, use_ai=False)
    assert any("Rebalance decided" in m for m in msgs)
    buys = [o for o in pf["pending"] if o["side"] == "buy"]
    assert len(buys) == 5 and "DOWN" not in {o["symbol"] for o in buys}
    d1 = dt.date(2026, 11, 5)
    momentum.run(pf, RULES, CAL, Fake(d1), now=at(d1, "16:30"), symbols=SYMS, use_ai=False)
    assert len(pf["positions"]) == 5 and pf["cash"] > 0
    assert all(p["stop"] < p["entry_price"] for p in pf["positions"])
    # catch up several days at once, through month end (30 Nov is the last trading day)
    d2 = dt.date(2026, 11, 30)
    msgs = momentum.run(pf, RULES, CAL, Fake(d2), now=at(d2, "16:30"), symbols=SYMS, use_ai=False)
    st = momentum.read_json(momentum.STATE_PATH)
    assert st["last_day"] == "2026-11-30"
    assert any("Rebalance decided on 2026-11-30" in m for m in msgs)
    assert pf["equity_history"][-1]["date"] == "2026-11-30"
