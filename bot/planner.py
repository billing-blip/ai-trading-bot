"""Morning snapshot (for the AI), plan validation, and plan activation."""
from __future__ import annotations

import datetime as dt
import subprocess

import pandas as pd

from . import portfolio as P
from .config import DATA_DIR, PLANS_DIR, ROOT, read_json, write_json
from .data import GLOBAL_CUES, DataProvider
from .indicators import add_all
from .news import headlines
from .risk import blocked_reason, bucket_available, can_add, period_pnl, size_trade
from .strategies import SWING_STRATEGIES, orb_eligible
from .timeutil import at, is_trading_day, now_ist
from .universe import fetch_nifty50

REGIMES = ["trending_up", "sideways", "falling", "high_risk"]
SNAP_DIR = DATA_DIR / "snapshots"


# ------------------------------------------------------------------ snapshot

def _pct(a, b):
    return round((a - b) / b * 100, 2) if b else None


def market_overview(provider: DataProvider) -> dict:
    out = {}
    for name, df in provider.index_daily(GLOBAL_CUES, period="3mo").items():
        if len(df) < 2:
            continue
        c = df["Close"]
        item = {"last": round(float(c.iloc[-1]), 2), "change_pct": _pct(c.iloc[-1], c.iloc[-2]),
                "as_of": str(pd.Timestamp(df.index[-1]).date())}
        if name == "Nifty 50" and len(c) >= 50:
            item["above_20d_avg"] = bool(c.iloc[-1] > c.rolling(20).mean().iloc[-1])
            item["above_50d_avg"] = bool(c.iloc[-1] > c.rolling(50).mean().iloc[-1])
            item["change_5d_pct"] = _pct(c.iloc[-1], c.iloc[-6])
        out[name] = item
    return out


def build_snapshot(provider: DataProvider, rules: dict, cal: dict, pf: dict, day: dt.date,
                   with_news: bool = True) -> dict:
    symbols = fetch_nifty50()
    daily = provider.daily(symbols, period="1y")
    blackout = {s for s, (a, b) in cal["blackout"].items() if a <= day <= b}
    candidates, orb = [], []
    for sym, df in daily.items():
        if sym in blackout or len(df) < 60:
            continue
        # only use candles from BEFORE today
        df = df[[d < day for d in df.index]]
        ind = add_all(df, rules)
        for strat in SWING_STRATEGIES:
            s = strat(sym, ind, rules)
            if not s:
                continue
            sz = size_trade(s, rules, rules["capital"]["swing_bucket"])
            if not sz:
                continue
            s.update(sz)
            s["id"] = f"{s['strategy'][:2].upper()}-{sym}"
            s["last_close"] = round(float(ind["Close"].iloc[-1]), 2)
            candidates.append(s)
        e = orb_eligible(sym, ind, rules)
        if e:
            e["last_close"] = round(float(ind["Close"].iloc[-1]), 2)
            orb.append(e)

    snap = {
        "date": day.isoformat(),
        "generated_at": now_ist().isoformat(),
        "is_trading_day": is_trading_day(day, cal),
        "calendar_no_trade_day": day in cal["no_trade_days"],
        "universe_size": len(daily),
        "market": market_overview(provider),
        "swing_candidates": candidates,
        "orb_eligible": orb,
        "blackout": sorted(blackout),
        "portfolio": {
            "cash": pf["cash"],
            "pnl": period_pnl(pf, rules, day),
            "open_positions": [
                {k: p[k] for k in ("id", "symbol", "strategy", "type", "qty", "entry_price", "stop",
                                   "target", "days_held", "last_price")} for p in pf["positions"]],
            "blocks": pf["blocks"],
        },
        "limits": rules["risk"],
        "max_stocks_in_plan": rules["ai"]["max_stocks_in_plan"],
    }
    if with_news:
        snap["headlines"] = headlines("Nifty Sensex stock market today", 8)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    write_json(SNAP_DIR / f"{day.isoformat()}.json", snap)
    write_json(SNAP_DIR / "latest.json", snap)
    return snap


# ------------------------------------------------------------------ plan validation

def plan_path(day: dt.date):
    return PLANS_DIR / f"{day.isoformat()}.json"


def validate_plan(plan: dict, snap: dict, rules: dict) -> tuple[list[str], dict]:
    """Returns (errors, cleaned_plan). Any error => plan rejected => no trades today."""
    errors = []
    if not isinstance(plan, dict):
        return ["plan is not a JSON object"], {}
    if plan.get("date") != snap.get("date"):
        errors.append(f"plan date {plan.get('date')} does not match today {snap.get('date')}")
    regime = plan.get("regime")
    if regime not in REGIMES:
        errors.append(f"regime must be one of {REGIMES}")
    cands = {c["id"]: c for c in snap.get("swing_candidates", [])}
    orb_ok = {c["symbol"] for c in snap.get("orb_eligible", [])}
    excluded = {e.get("symbol") for e in plan.get("excluded", []) if isinstance(e, dict)}

    selected = []
    for item in plan.get("selected", []) or []:
        cid = item.get("id") if isinstance(item, dict) else None
        if cid not in cands:
            errors.append(f"selected id {cid!r} is not one of today's candidates")
            continue
        if cands[cid]["symbol"] in excluded:
            continue
        if not item.get("reason"):
            errors.append(f"selected {cid} has no reason")
        selected.append({"id": cid, "priority": int(item.get("priority", 99)), "reason": item.get("reason", "")})
    selected.sort(key=lambda x: x["priority"])

    orb_sel = []
    allowed_regimes = rules["strategies"]["opening_range_breakout"]["only_when_regime"]
    for item in plan.get("orb_selected", []) or []:
        sym = item.get("symbol") if isinstance(item, dict) else None
        if sym not in orb_ok:
            errors.append(f"ORB symbol {sym!r} is not eligible today")
            continue
        if regime not in allowed_regimes:
            errors.append(f"ORB trades only allowed when regime is {allowed_regimes}")
            continue
        if sym in excluded:
            continue
        orb_sel.append({"symbol": sym, "reason": item.get("reason", "")})

    if len(selected) + len(orb_sel) > rules["ai"]["max_stocks_in_plan"]:
        errors.append(f"plan picks more than {rules['ai']['max_stocks_in_plan']} stocks")
    no_trade = bool(plan.get("no_trade")) or regime == "high_risk"
    cleaned = {
        "date": plan.get("date"), "regime": regime, "no_trade": no_trade,
        "no_trade_reason": plan.get("no_trade_reason", "regime high_risk" if regime == "high_risk" else ""),
        "selected": [] if no_trade else selected,
        "orb_selected": [] if no_trade else orb_sel,
        "excluded": plan.get("excluded", []), "summary": plan.get("summary", ""),
    }
    return errors, cleaned


def plan_commit_time(day: dt.date) -> dt.datetime | None:
    """When the plan file was committed (from git). Used for the 09:00 deadline."""
    path = plan_path(day)
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", str(path.relative_to(ROOT))],
                             cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
        return dt.datetime.fromisoformat(out) if out else None
    except Exception:
        return None


# ------------------------------------------------------------------ activation

def activate(pf: dict, rules: dict, cal: dict, control: dict, day: dt.date, now: dt.datetime) -> list[str]:
    msgs = []
    if pf.get("activated_date") == day.isoformat():
        return ["already activated today"]
    pf["activated_date"] = day.isoformat()
    pf["orb_today"] = {"date": day.isoformat(), "symbols": [], "done": []}

    def no_trade(reason):
        pf["blocks"]["no_trade_day"] = day.isoformat()
        pf["blocks"]["no_trade_reason"] = reason
        P.log(pf, "NO-TRADE", reason, now)
        msgs.append(f"No trades today: {reason}")

    if not is_trading_day(day, cal):
        return ["market holiday"]
    if day in cal["no_trade_days"]:
        no_trade("scheduled big-event day (calendar)")
        return msgs
    snap = read_json(SNAP_DIR / f"{day.isoformat()}.json")
    plan = read_json(plan_path(day))
    if not snap:
        no_trade("no market snapshot for today")
        return msgs
    if not plan:
        no_trade("no AI plan arrived")
        return msgs
    deadline = at(day, rules["schedule"]["plan_deadline"])
    committed = plan_commit_time(day)
    stamp = committed or (dt.datetime.fromisoformat(plan["created_at"]) if plan.get("created_at") else None)
    if stamp is None or stamp > deadline:
        no_trade(f"AI plan arrived late ({stamp}) — deadline {deadline.time()}")
        return msgs
    errors, plan = validate_plan(plan, snap, rules)
    if errors:
        no_trade("AI plan failed validation: " + "; ".join(errors[:5]))
        return msgs
    if plan["no_trade"]:
        no_trade(f"AI chose no-trade: {plan['no_trade_reason']}")
        return msgs
    why = blocked_reason(pf, control, day)
    if why:
        P.log(pf, "BLOCKED", f"plan not activated — {why}", now)
        return [f"Blocked: {why}"]

    cands = {c["id"]: c for c in snap["swing_candidates"]}
    for item in plan["selected"]:
        c = cands[item["id"]]
        sz = size_trade(c, rules, bucket_available(pf, rules, "swing"))
        if not sz:
            P.log(pf, "SKIP", f"{c['symbol']} — not enough swing capital", now)
            continue
        order = {**c, **sz, "id": P.new_id(), "plan_date": day.isoformat(), "reason": item["reason"],
                 "last_processed": at(day, "09:00").isoformat()}
        reason = can_add(pf, rules, order, day)
        if reason:
            P.log(pf, "SKIP", f"{c['symbol']} — {reason}", now)
            continue
        pf["pending"].append(order)
        P.log(pf, "ORDER", f"Pending BUY {order['qty']} {order['symbol']} above {order['entry']} "
                           f"(stop {order['stop']}, target {order['target']}) — {item['reason']}", now)
        msgs.append(f"Order: {order['symbol']} {order['strategy']}")
    pf["orb_today"]["symbols"] = [x["symbol"] for x in plan["orb_selected"]]
    pf["orb_today"]["reasons"] = {x["symbol"]: x["reason"] for x in plan["orb_selected"]}
    pf["regime_today"] = plan["regime"]
    return msgs
