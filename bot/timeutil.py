"""Indian market time helpers."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def today_ist() -> dt.date:
    return now_ist().date()


def parse_hhmm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def at(day: dt.date, hhmm: str) -> dt.datetime:
    return dt.datetime.combine(day, parse_hhmm(hhmm), tzinfo=IST)


def is_trading_day(day: dt.date, calendar: dict) -> bool:
    return day.weekday() < 5 and day not in calendar["holidays"]


def previous_trading_day(day: dt.date, calendar: dict) -> dt.date:
    d = day - dt.timedelta(days=1)
    while not is_trading_day(d, calendar):
        d -= dt.timedelta(days=1)
    return d


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def to_ist(ts) -> dt.datetime:
    """Accepts a pandas Timestamp / datetime (naive = UTC) and returns IST datetime."""
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(IST)
