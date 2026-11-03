"""Simple chart indicators used by the strategies."""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def add_all(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    s = rules["strategies"]
    out = df.copy()
    out["sma_trend"] = sma(out["Close"], s["trend_pullback"]["trend_sma"])
    out["sma_pull"] = sma(out["Close"], s["trend_pullback"]["pullback_sma"])
    lb = s["breakout"]["lookback_days"]
    out["high_n"] = out["High"].rolling(lb, min_periods=lb).max()
    out["vol_avg"] = out["Volume"].rolling(lb, min_periods=lb).mean()
    out["atr"] = atr(out, s["breakout"]["atr_period"])
    return out
