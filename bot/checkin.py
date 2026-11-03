"""Hourly AI check-in. The AI may ONLY reduce risk — every reply is validated in code."""
from __future__ import annotations

import datetime as dt

from . import llm
from . import portfolio as P
from .news import headlines
from .risk import period_pnl

SYSTEM = """You are the risk monitor for a small, rule-based paper-trading bot on Indian (NSE) stocks.
You review open trades once an hour. You can ONLY reduce risk. Allowed actions:
  - "exit": close an open position now (give its id)
  - "tighten_stop": raise the stop-loss of an open position (give id and new_stop; must be above the
    current stop and below the current price)
  - "cancel_entry": cancel a pending entry order (give its id)
  - "halt_day": stop all new trades for the rest of today
You can NOT open trades, widen stops, raise targets or change any limit.
Default to doing nothing. Act only on clear reasons: serious negative company news, a sharp market
sell-off, or a trade that clearly no longer makes sense. Normal price wiggles are NOT a reason.
Reply with ONLY a JSON object:
{"actions": [{"type": "...", "id": "...", "new_stop": 0, "reason": "..."}], "comment": "one short sentence"}
Use an empty actions list when nothing needs doing."""


def build_prompt(pf: dict, rules: dict, day: dt.date, now: dt.datetime, nifty_change_pct: float | None) -> str:
    lines = [f"Time: {now:%Y-%m-%d %H:%M} IST. Market regime chosen this morning: {pf.get('regime_today', 'unknown')}."]
    if nifty_change_pct is not None:
        lines.append(f"Nifty 50 change today: {nifty_change_pct:+.2f}%")
    pnl = period_pnl(pf, rules, day)
    lines.append(f"P&L today: {pnl['day']:+.0f} INR (daily limit -{rules['risk']['daily_loss_limit']}).")
    lines.append("\nOPEN POSITIONS:")
    for p in pf["positions"]:
        unreal = (p["last_price"] - p["entry_price"]) * p["qty"]
        lines.append(f"- id={p['id']} {p['symbol']} {p['type']} {p['strategy']} qty={p['qty']} entry={p['entry_price']} "
                     f"now={p['last_price']} stop={p['stop']} target={p['target']} unrealized={unreal:+.0f} "
                     f"days_held={p.get('days_held', 0)}")
    if not pf["positions"]:
        lines.append("- none")
    lines.append("\nPENDING ENTRY ORDERS:")
    for o in pf["pending"]:
        lines.append(f"- id={o['id']} {o['symbol']} {o['type']} buy above {o['entry']} stop={o['stop']}")
    if not pf["pending"]:
        lines.append("- none")
    lines.append("\nLATEST HEADLINES:")
    seen = set()
    for sym in {x["symbol"] for x in pf["positions"] + pf["pending"]}:
        for h in headlines(f"{sym} share NSE", 3):
            if h["title"] not in seen:
                seen.add(h["title"])
                lines.append(f"- [{sym}] {h['title']} ({h['published']})")
    for h in headlines("Nifty stock market today", 4):
        if h["title"] not in seen:
            lines.append(f"- [market] {h['title']} ({h['published']})")
    return "\n".join(lines)


def apply_actions(pf: dict, rules: dict, reply: dict, now: dt.datetime, day: dt.date) -> list[str]:
    allowed = set(rules["ai"]["allowed_checkin_actions"])
    done = []
    for a in reply.get("actions", []) or []:
        if not isinstance(a, dict):
            continue
        typ, rid, why = a.get("type"), a.get("id"), (a.get("reason") or "")[:200]
        if typ not in allowed:
            P.log(pf, "AI-REJECTED", f"action {typ!r} not allowed", now)
            continue
        if typ == "halt_day":
            pf["blocks"]["no_trade_day"] = day.isoformat()
            pf["blocks"]["no_trade_reason"] = f"AI halted trading: {why}"
            for o in pf["pending"]:
                P.log(pf, "CANCEL", f"{o['symbol']} entry cancelled — AI halt", now)
            pf["pending"] = []
            done.append(f"AI halted new trades today: {why}")
            continue
        if typ == "cancel_entry":
            o = next((x for x in pf["pending"] if x["id"] == rid), None)
            if o:
                pf["pending"].remove(o)
                P.log(pf, "AI", f"cancelled {o['symbol']} entry — {why}", now)
                done.append(f"AI cancelled {o['symbol']} entry")
            continue
        pos = next((x for x in pf["positions"] if x["id"] == rid), None)
        if not pos:
            P.log(pf, "AI-REJECTED", f"{typ}: unknown id {rid}", now)
            continue
        if typ == "exit":
            t = P.close_position(pf, pos, pos["last_price"], now, f"AI early exit: {why}", rules)
            done.append(f"AI exited {t['symbol']} (net {t['net_pnl']:+.0f}): {why}")
        elif typ == "tighten_stop":
            try:
                ns = round(float(a.get("new_stop")), 2)
            except (TypeError, ValueError):
                P.log(pf, "AI-REJECTED", f"tighten_stop {pos['symbol']}: bad new_stop", now)
                continue
            if pos["stop"] < ns < pos["last_price"] * 0.998:
                P.log(pf, "AI", f"{pos['symbol']} stop raised {pos['stop']} -> {ns} — {why}", now)
                pos["stop"] = ns
                done.append(f"AI tightened {pos['symbol']} stop to {ns}")
            else:
                P.log(pf, "AI-REJECTED", f"tighten_stop {pos['symbol']} to {ns} not allowed (must be > {pos['stop']} and below price)", now)
    if reply.get("comment"):
        P.log(pf, "AI-NOTE", str(reply["comment"])[:300], now)
    return done


def run(pf: dict, rules: dict, day: dt.date, now: dt.datetime, nifty_change_pct: float | None) -> list[str]:
    if not (pf["positions"] or pf["pending"]):
        return []
    if not llm.configured():
        P.log(pf, "AI-SKIP", "check-in skipped: API key not configured", now)
        return []
    prompt = build_prompt(pf, rules, day, now, nifty_change_pct)
    try:
        text = llm.chat(SYSTEM, prompt, rules["ai"]["checkin_model"], rules["ai"]["checkin_max_tokens"])
        reply = llm.extract_json(text)
    except Exception as e:  # AI problems must never break the bot
        P.log(pf, "AI-ERROR", f"check-in failed: {str(e)[:200]} — rules keep running without AI", now)
        return []
    return apply_actions(pf, rules, reply, now, day)
