import datetime as dt
import time

import numpy as np
import pandas as pd

from backtest.run_backtest import orb_backtest, swing_backtest, write_summary
from bot.config import load_rules
from bot.timeutil import at

RULES = load_rules()


def walk(n, seed, drift=0.0005):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2021-01-01", periods=n).date
    close = 500 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    high = close * (1 + rng.uniform(0, 0.015, n))
    low = close * (1 - rng.uniform(0, 0.015, n))
    opn = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.004, n))
    vol = rng.integers(5e5, 3e6, n).astype(float)
    return pd.DataFrame({"Open": opn, "High": np.maximum.reduce([high, opn, close]),
                         "Low": np.minimum.reduce([low, opn, close]), "Close": close, "Volume": vol}, index=days)


def test_swing_backtest_runs_and_respects_limits():
    daily = {f"S{i}": walk(400, i) for i in range(12)}
    t0 = time.time()
    res = swing_backtest(daily, RULES)
    assert time.time() - t0 < 120
    trades = res["trades"]
    assert trades, "expected some trades on random data"
    # gap-throughs can exceed the planned loss; ordinary stop-outs must not
    for t in trades:
        if t["exit_reason"] in ("stop-loss", "stop-loss (same day)"):
            assert t["net_pnl"] >= -RULES["risk"]["max_loss_per_trade"] - 1
    text = write_summary({"test": res}, RULES)
    assert "Verdict" in text


def test_orb_backtest_runs():
    day = dt.date(2026, 10, 1)
    daily = {"S0": walk(200, 1, drift=0.003)}
    daily["S0"].index = pd.bdate_range(end=day - dt.timedelta(days=1), periods=200).date
    rows, px, t = [], 1000.0, at(day, "09:15")
    while t < at(day, "15:30"):
        rows.append((t, px, px * 1.002, px * 0.999, px * 1.001))
        px *= 1.001
        t += pd.Timedelta(minutes=5)
    intra = {"S0": pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
                                 "Low": [r[3] for r in rows], "Close": [r[4] for r in rows], "Volume": 1},
                                index=pd.DatetimeIndex([r[0] for r in rows]))}
    res = orb_backtest(intra, daily, RULES, nifty=None)
    assert isinstance(res["trades"], list)
