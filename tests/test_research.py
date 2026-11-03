import numpy as np
import pandas as pd

from backtest.research import run_all, write_report
from bot.config import load_rules
from tests.test_backtest import walk


def test_research_runs_all_variants():
    rules = load_rules()
    daily = {f"S{i}": walk(1100, i, drift=0.0006) for i in range(10)}
    idx = list(daily["S0"].index)
    nifty = pd.Series(np.linspace(17000, 24000, len(idx)), index=idx)
    out = run_all(daily, nifty, rules)
    assert len(out) == 9
    for r in out.values():
        assert r["verdict"] in {"PASS", "WEAK", "FAIL", "NOT ENOUGH TRADES"}
    assert "Benchmark" in write_report(out, nifty)
