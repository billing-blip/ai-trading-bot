"""Nifty 50 stock list.

The bot tries to download the official list from NSE each morning. If NSE blocks the
request, it falls back to the cached copy (state/nifty50.json), and finally to the
built-in list below. The built-in list may be slightly out of date after NSE's
twice-yearly index changes, which is why the live download comes first.
"""
from __future__ import annotations

import csv
import io

import requests

from .config import STATE_DIR, read_json, write_json

NSE_CSV = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"

FALLBACK = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
    "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN", "TMPV", "TRENT",
    "ULTRACEMCO", "WIPRO",
]


def fetch_nifty50() -> list[str]:
    cache = STATE_DIR / "nifty50.json"
    try:
        r = requests.get(
            NSE_CSV,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv"},
            timeout=20,
        )
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        symbols = [row["Symbol"].strip() for row in rows if row.get("Symbol")]
        if 45 <= len(symbols) <= 55:
            write_json(cache, {"symbols": symbols, "source": "nse"})
            return symbols
    except Exception:
        pass
    cached = read_json(cache)
    if cached and cached.get("symbols"):
        return cached["symbols"]
    return list(FALLBACK)


def yahoo_ticker(symbol: str) -> str:
    return symbol if symbol.startswith("^") else f"{symbol}.NS"
