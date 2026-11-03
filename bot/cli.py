"""Command line entry point. GitHub Actions runs these on a timetable.

  python -m bot.cli snapshot        08:00  prepare market data + valid trade candidates for the AI
  python -m bot.cli validate-plan   (used by the morning AI to check its own plan before saving)
  python -m bot.cli activate        09:10  check the AI plan and turn it into pending orders
  python -m bot.cli tick            hourly simulate fills/stops/targets, set up ORB trades, AI check-in
  python -m bot.cli eod             16:00  close out the day, write reports
  python -m bot.cli status          print a quick summary
  python -m bot.cli pause|resume    stop / restart new trades
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pandas as pd

from . import checkin, planner, report, simulator
from . import portfolio as P
from .config import load_calendar, load_rules, read_json, write_json
from .data import get_provider
from .risk import (apply_loss_limits, blocked_reason, bucket_available, can_add, equity,
                   open_risk, period_pnl, size_trade)
from .strategies import orb_setup
from .timeutil import at, is_trading_day, now_ist


def _ctx():
    rules, cal = load_rules(), load_calendar()
    pf, control = P.load(rules), P.load_control()
    return rules, cal, pf, control


def cmd_snapshot(args):
    rules, cal, pf, _ = _ctx()
    day = args.day or now_ist().date()
    if not is_trading_day(day, cal):
        print(f"{day} is not a trading day — no snapshot.")
        return
    if (planner.SNAP_DIR / f"{day.isoformat()}.json").exists() and not args.force:
        print("snapshot already exists for today (use --force to rebuild)")
        return
    snap =planner.build_snapshot(get_provider(), rules, cal, pf, day, with_news=not args.no_news)
    print(f"Snapshot {day}: {len(snap['swing_candidates'])} swing candidates, "
          f"{len(snap['orb_eligible'])} ORB-eligible, universe {snap['universe_size']}")


def cmd_validate_plan(args):
    rules = load_rules()
    day = args.day or now_ist().date()
    snap = read_json(planner.SNAP_DIR / f"{day.isoformat()}.json")
    plan = read_json(planner.plan_path(day))
    if not snap or not plan:
        print("MISSING:", "snapshot" if not snap else "plan")
        sys.exit(1)
    errors, cleaned = planner.validate_plan(plan, snap, rules)
    if errors:
        print("INVALID PLAN:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("PLAN OK:", json.dumps(cleaned, indent=2))


def cmd_activate(args):
    rules, cal, pf, control = _ctx()
    now = now_ist()
    msgs = planner.activate(pf, rules, cal, control, now.date(), now)
    P.save(pf)
    print("\n".join(msgs) or "nothing to do")


def _nifty_info(candles, snap):
    df = candles.get("^NSEI")
    prev = (snap or {}).get("market", {}).get("Nifty 50", {}).get("last")
    if df is None or df.empty or not prev:
        return prev, None, None
    return prev, float(df["Open"].iloc[0]), round((float(df["Close"].iloc[-1]) - prev) / prev * 100, 2)


def _setup_orb(pf, rules, control, day, now, candles):
    orb = pf.get("orb_today") or {}
    if orb.get("date") != day.isoformat():
        return
    ready = at(day, "09:15") + pd.Timedelta(minutes=rules["strategies"]["opening_range_breakout"]["range_minutes"] + 5)
    if now < ready:
        return
    for sym in orb.get("symbols", []):
        if sym in orb.get("done", []):
            continue
        orb.setdefault("done", []).append(sym)
        df = candles.get(sym)
        if df is None:
            continue
        s = orb_setup(sym, df, day, rules)
        if not s:
            P.log(pf, "SKIP", f"{sym} ORB — opening range too wide or data missing", now)
            continue
        why = blocked_reason(pf, control, day)
        if why:
            P.log(pf, "SKIP", f"{sym} ORB — {why}", now)
            continue
        sz = size_trade(s, rules, bucket_available(pf, rules, "intraday"))
        if not sz:
            P.log(pf, "SKIP", f"{sym} ORB — not enough intraday capital", now)
            continue
        order = {**s, **sz, "id": P.new_id(), "plan_date": day.isoformat(),
                 "reason": orb.get("reasons", {}).get(sym, ""),
                 "last_processed": (pd.Timestamp(s["active_from"]) - pd.Timedelta(seconds=1)).isoformat()}
        reason = can_add(pf, rules, order, day)
        if reason:
            P.log(pf, "SKIP", f"{sym} ORB — {reason}", now)
            continue
        pf["pending"].append(order)
        P.log(pf, "ORDER", f"Pending ORB BUY {order['qty']} {sym} above {order['entry']} (stop {order['stop']}, "
                           f"target {order['target']})", now)


def cmd_tick(args):
    rules, cal, pf, control = _ctx()
    now = args.now or now_ist()
    day = now.date()
    if not is_trading_day(day, cal) or now < at(day, "09:15"):
        print("market closed")
        return
    snap = read_json(planner.SNAP_DIR / f"{day.isoformat()}.json")
    syms = {p["symbol"] for p in pf["positions"]} | {o["symbol"] for o in pf["pending"]}
    if (pf.get("orb_today") or {}).get("date") == day.isoformat():
        syms |= set(pf["orb_today"].get("symbols", []))
    candles = get_provider().intraday(sorted(syms | {"^NSEI"}), day) if syms else get_provider().intraday(["^NSEI"], day)
    prev, nifty_open, nifty_chg = _nifty_info(candles, snap)
    if pf.get("nifty_gap_checked") != day.isoformat() and nifty_open:
        pf["nifty_gap_checked"] = day.isoformat()
        msg = simulator.nifty_gap_check(pf, rules, day, prev, nifty_open)
        if msg:
            P.log(pf, "NO-TRADE", msg, now)
    _setup_orb(pf, rules, control, day, now, candles)
    alerts = simulator.run(pf, rules, control, day, {k: v for k, v in candles.items() if k != "^NSEI"}, now)
    for msg in apply_loss_limits(pf, rules, day):
        alerts.append(msg)
        P.log(pf, "LIMIT", msg, now)
        simulator.cancel_all_pending(pf, msg, now)
    if now <= at(day, "15:30"):
        alerts += checkin.run(pf, rules, day, now, nifty_chg)
    P.save(pf)
    print("\n".join(alerts) or "no changes")


def audit(pf, rules, day, now):
    """Sanity checks. Anything here is a bug in the bot, logged as RULE-BREAK."""
    problems = []
    if any(p["type"] == "intraday" for p in pf["positions"]):
        problems.append("intraday position still open after the close")
    if open_risk(pf) > rules["risk"]["max_total_open_risk"] + 1:
        problems.append(f"open risk {open_risk(pf)} above limit")
    if sum(1 for p in pf["positions"] if p["type"] == "swing") > rules["risk"]["max_open_swing_trades"]:
        problems.append("too many swing positions")
    for p in pf["positions"]:
        if not p.get("stop"):
            problems.append(f"{p['symbol']} has no stop-loss")
    for msg in problems:
        P.log(pf, "RULE-BREAK", msg, now)
    return problems


def cmd_eod(args):
    rules, cal, pf, control = _ctx()
    now = args.now or now_ist()
    day = now.date()
    if not is_trading_day(day, cal):
        print("not a trading day")
        return
    if pf.get("eod_done") == day.isoformat():
        print("EOD already done")
        return
    syms = sorted({p["symbol"] for p in pf["positions"]} | {o["symbol"] for o in pf["pending"]})
    candles = get_provider().intraday(syms, day) if syms else {}
    close_time = at(day, "15:31")
    alerts = simulator.run(pf, rules, control, day, candles, close_time)
    closes = {s: float(df["Close"].iloc[-1]) for s, df in candles.items() if not df.empty}
    alerts += simulator.end_of_day(pf, rules, day, closes, now)
    alerts += apply_loss_limits(pf, rules, day)
    audit(pf, rules, day, now)
    eq = equity(pf, rules)
    pf["equity_history"] = [r for r in pf["equity_history"] if r["date"] != day.isoformat()]
    pf["equity_history"].append({"date": day.isoformat(), "equity": eq, "cash": pf["cash"],
                                 "open_positions": len(pf["positions"])})
    pf["eod_done"] = day.isoformat()
    report.write_all(pf, rules, day)
    P.save(pf)
    print("\n".join(alerts) or "EOD done")
    print(report.daily(pf, rules, day))


def cmd_status(args):
    rules, cal, pf, control = _ctx()
    day = now_ist().date()
    print(json.dumps({"pnl": period_pnl(pf, rules, day), "cash": pf["cash"],
                      "positions": [(p["symbol"], p["qty"], p["entry_price"], p["stop"], p["target"]) for p in pf["positions"]],
                      "pending": [(o["symbol"], o["entry"]) for o in pf["pending"]],
                      "blocks": pf["blocks"], "paused": control.get("paused")}, indent=2))


def cmd_pause(args, value=True):
    from .portfolio import CONTROL_PATH
    c = P.load_control()
    c["paused"] = value
    c["changed"] = now_ist().isoformat()
    write_json(CONTROL_PATH, c)
    print("PAUSED — no new trades" if value else "RESUMED")


def _parse_now(s: str) -> dt.datetime:
    from .timeutil import IST
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=IST)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ["snapshot", "validate-plan", "activate", "tick", "eod", "status", "pause", "resume"]:
        p = sub.add_parser(name)
        p.add_argument("--day", type=dt.date.fromisoformat, default=None)
        p.add_argument("--now", type=_parse_now, default=None)
        p.add_argument("--no-news", action="store_true")
        p.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    {
        "snapshot": cmd_snapshot, "validate-plan": cmd_validate_plan, "activate": cmd_activate,
        "tick": cmd_tick, "eod": cmd_eod, "status": cmd_status,
        "pause": lambda x: cmd_pause(x, True), "resume": lambda x: cmd_pause(x, False),
    }[a.cmd](a)


if __name__ == "__main__":
    main()
