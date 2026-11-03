"""Market data. Free Yahoo Finance data for paper trading.

Everything else in the bot talks to a `DataProvider`, so the source can later be
swapped for Upstox/Dhan without touching strategy or simulation code.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .timeutil import IST
from .universe import yahoo_ticker

COLS = ["Open", "High", "Low", "Close", "Volume"]

GLOBAL_CUES = {
    "Nifty 50": "^NSEI",
    "Bank Nifty": "^NSEBANK",
    "India VIX": "^INDIAVIX",
    "S&P 500 (US)": "^GSPC",
    "Nasdaq (US)": "^IXIC",
    "Nikkei (Japan)": "^N225",
    "Hang Seng (HK)": "^HSI",
    "Crude oil": "CL=F",
    "USD/INR": "INR=X",
}


class DataProvider:
    def daily(self, symbols: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
        raise NotImplementedError

    def intraday(self, symbols: list[str], day: dt.date, interval: str = "5m") -> dict[str, pd.DataFrame]:
        raise NotImplementedError

    def index_daily(self, tickers: dict[str, str], period: str = "10d") -> dict[str, pd.DataFrame]:
        raise NotImplementedError


def _split(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in lvl0:
                df = raw[t]
            else:
                try:
                    df = raw.xs(t, axis=1, level=1)
                except KeyError:
                    continue
            df = df[[c for c in COLS if c in df.columns]].dropna(how="all")
            if not df.empty:
                out[t] = df
    else:
        df = raw[[c for c in COLS if c in raw.columns]].dropna(how="all")
        if not df.empty and tickers:
            out[tickers[0]] = df
    return out


class YahooProvider(DataProvider):
    def _download(self, tickers, **kw):
        import time

        import yfinance as yf

        last_err = None
        for attempt in range(4):           # Yahoo sometimes rate-limits; wait and retry
            try:
                kw.setdefault("auto_adjust", False)
                raw = yf.download(tickers, group_by="ticker", progress=False, threads=True, **kw)
                if raw is not None and not raw.empty:
                    return raw
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(10 * (attempt + 1))
        print(f"WARNING: no data from Yahoo for {len(tickers)} tickers ({last_err})")
        return pd.DataFrame()

    def daily(self, symbols, period="1y"):
        tick = [yahoo_ticker(s) for s in symbols]
        # adjusted prices: stock splits / bonus issues must not look like crashes
        raw = self._download(tick, period=period, interval="1d", auto_adjust=True)
        by_t = _split(raw, tick)
        out = {}
        for s, t in zip(symbols, tick):
            if t in by_t:
                df = by_t[t].copy()
                df.index = pd.to_datetime(df.index).date
                out[s] = df.dropna(subset=["Close"])
        return out

    def intraday(self, symbols, day, interval="5m"):
        tick = [yahoo_ticker(s) for s in symbols]
        start = day
        end = day + dt.timedelta(days=1)
        raw = self._download(tick, start=start.isoformat(), end=end.isoformat(), interval=interval)
        by_t = _split(raw, tick)
        out = {}
        for s, t in zip(symbols, tick):
            if t in by_t:
                df = by_t[t].copy()
                idx = pd.to_datetime(df.index)
                idx = idx.tz_localize("UTC") if idx.tz is None else idx
                df.index = idx.tz_convert(IST)
                df = df[df.index.date == day].dropna(subset=["Close"])
                out[s] = df
        return out

    def index_daily(self, tickers, period="10d"):
        names = list(tickers)
        tick = [tickers[n] for n in names]
        raw = self._download(tick, period=period, interval="1d")
        by_t = _split(raw, tick)
        return {n: by_t[tickers[n]].dropna(subset=["Close"]) for n in names if tickers[n] in by_t}

    def history(self, symbols, period="5y"):
        return self.daily(symbols, period=period)

    def intraday_history(self, symbols, period="60d", interval="5m"):
        tick = [yahoo_ticker(s) for s in symbols]
        raw = self._download(tick, period=period, interval=interval)
        by_t = _split(raw, tick)
        out = {}
        for s, t in zip(symbols, tick):
            if t in by_t:
                df = by_t[t].copy()
                idx = pd.to_datetime(df.index)
                idx = idx.tz_localize("UTC") if idx.tz is None else idx
                df.index = idx.tz_convert(IST)
                out[s] = df.dropna(subset=["Close"])
        return out


def get_provider() -> DataProvider:
    return YahooProvider()
