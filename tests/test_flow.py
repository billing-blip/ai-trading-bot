"""Full day, end to end, with fake market data (no internet needed)."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from bot import cli, planner
from bot import portfolio as P
from bot.config import PLANS_DIR, REPORTS_DIR
from bot.data import DataProvider
from bot.timeutil import at
from tests.test_engine import DAY, bars, synthetic_daily


class FakeProvider(DataProvider):
    def daily(self, symbols, period="1y"):
        out = {}
        for i, s in enumerate(symbols):
            out[s] = synthetic_daily(breakout_last=(s == "BRK"), seed=i + 3)
        return out

    def intraday(self, symbols, day, interval="5m"):
        out = {}
        for s in symbols:
            if s == "^NSEI":
                out[s] = bars(day, [("09:15", 24010, 24050, 23990, 24030), ("09:20", 24030, 24060, 24020, 24050)])
                continue
            daily = synthetic_daily(breakout_last=(s == "BRK"), seed=3)
            last_high = float(daily["High"].iloc[-1])
            px = last_high
            rows, t = [], at(day, "09:15")
            while t < at(day, "15:30"):
                rows.append((t.strftime("%H:%M"), px, px * 1.004, px * 0.999, px * 1.003))
                px *= 1.003
                t += pd.Timedelta(minutes=5)
            out[s] = bars(day, rows)
        return out

    def index_daily(self, tickers, period="10d"):
        idx = [DAY - dt.timedelta(days=60 - i) for i in range(60)]
        c = pd.Series(np.linspace(23000, 24000, 60), index=idx)
        df = pd.DataFrame({"Open": c, "High": c + 50, "Low": c - 50, "Close": c, "Volume": 1})
        return {n: df for n in tickers}


def test_full_day(monkeypatch):
    fake = FakeProvider()
    real_rules = cli.load_rules
    monkeypatch.setattr(cli, "load_rules", lambda: {**real_rules(), "strategy_mode": "swing"})
    monkeypatch.setattr(cli, "get_provider", lambda: fake)
    monkeypatch.setattr(planner, "fetch_nifty50", lambda: ["BRK", "AAA", "BBB"])
    monkeypatch.setattr(planner, "headlines", lambda *a, **k: [])
    monkeypatch.setattr(planner, "now_ist", lambda: at(DAY, "08:00"))

    cli.main(["snapshot", "--day", DAY.isoformat(), "--no-news"])
    snap = json.loads((planner.SNAP_DIR / f"{DAY}.json").read_text())
    brk = [c for c in snap["swing_candidates"] if c["symbol"] == "BRK" and c["strategy"] == "breakout"]
    assert brk, "breakout candidate expected"

    plan = {"date": DAY.isoformat(), "created_at": at(DAY, "08:40").isoformat(), "regime": "trending_up",
            "selected": [{"id": brk[0]["id"], "priority": 1, "reason": "test breakout"}],
            "orb_selected": [], "excluded": [], "summary": "test"}
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    (PLANS_DIR / f"{DAY}.json").write_text(json.dumps(plan))
    cli.main(["validate-plan", "--day", DAY.isoformat()])

    monkeypatch.setattr(cli, "now_ist", lambda: at(DAY, "09:10"))
    cli.main(["activate"])
    rules = cli.load_rules()
    pf = P.load(rules)
    assert len(pf["pending"]) == 1

    cli.main(["tick", "--now", at(DAY, "10:50").isoformat()])
    pf = P.load(rules)
    assert pf["positions"] or pf["closed"], "order should have filled on a rising day"

    cli.main(["eod", "--now", at(DAY, "16:00").isoformat()])
    pf = P.load(rules)
    assert pf["eod_done"] == DAY.isoformat()
    assert pf["equity_history"][-1]["date"] == DAY.isoformat()
    assert (REPORTS_DIR / "latest.md").exists()
    assert not any(e["kind"] == "RULE-BREAK" for e in pf["events"])
