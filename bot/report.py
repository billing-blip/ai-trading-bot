"""Daily / weekly reports and the dashboard data file."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .config import REPORTS_DIR, write_json
from .risk import equity, period_pnl


def stats(trades: list[dict], starting_capital: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net_pnl": 0, "win_rate": None, "profit_factor": None,
                "avg_win": None, "avg_loss": None, "max_drawdown_pct": 0, "charges": 0}
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trades if t["net_pnl"] <= 0]
    eq, peak, mdd = starting_capital, starting_capital, 0.0
    for t in sorted(trades, key=lambda x: x["exit_time"]):
        eq += t["net_pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    gross_loss = -sum(losses)
    return {
        "trades": n,
        "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_pct": round(mdd, 2),
        "charges": round(sum(t.get("charges", 0) for t in trades), 2),
    }


def by_strategy(trades: list[dict], starting_capital: float) -> dict:
    groups = defaultdict(list)
    for t in trades:
        groups[t["strategy"]].append(t)
    return {k: stats(v, starting_capital) for k, v in groups.items()}


def go_live_check(pf: dict, rules: dict) -> dict:
    g = rules["go_live_criteria"]
    s = stats(pf["closed"], pf["starting_capital"])
    days = len(pf["equity_history"])
    rule_breaks = sum(1 for e in pf["events"] if e["kind"] == "RULE-BREAK")
    checks = {
        f"at least {g['min_paper_days']} trading days": days >= g["min_paper_days"],
        f"at least {g['min_trades']} trades": s["trades"] >= g["min_trades"],
        "profit after charges": s["net_pnl"] > 0,
        f"profit factor >= {g['min_profit_factor']}": (s["profit_factor"] or 0) >= g["min_profit_factor"],
        f"max drawdown < {g['max_drawdown_pct']}%": s["max_drawdown_pct"] < g["max_drawdown_pct"],
        "zero rule breaks": rule_breaks == 0,
    }
    return {"checks": checks, "ready": all(checks.values()), "days": days}


def daily(pf: dict, rules: dict, day: dt.date) -> str:
    today = [t for t in pf["closed"] if t["exit_time"][:10] == day.isoformat()]
    pnl = period_pnl(pf, rules, day)
    s_all = stats(pf["closed"], pf["starting_capital"])
    ev = [e for e in pf["events"] if e["time"][:10] == day.isoformat()]
    L = [f"# Daily report — {day:%A %d %b %Y}", ""]
    L.append(f"**Equity:** ₹{pnl['equity']:,.0f}  |  **Today:** {pnl['day']:+,.0f}  |  "
             f"**This week:** {pnl['week']:+,.0f}  |  **This month:** {pnl['month']:+,.0f}")
    L.append(f"**Regime today:** {pf.get('regime_today', '—')}")
    if pf["blocks"].get("no_trade_day") == day.isoformat():
        L.append(f"**No-trade day:** {pf['blocks'].get('no_trade_reason')}")
    L += ["", "## Trades closed today"]
    if today:
        L.append("| Stock | Strategy | Qty | Entry | Exit | Net P&L | Why exited |")
        L.append("|---|---|---|---|---|---|---|")
        for t in today:
            L.append(f"| {t['symbol']} | {t['strategy']} | {t['qty']} | {t['entry_price']} | {t['exit_price']} | "
                     f"{t['net_pnl']:+.0f} | {t['exit_reason']} |")
    else:
        L.append("None.")
    L += ["", "## Open positions"]
    if pf["positions"]:
        L.append("| Stock | Type | Qty | Entry | Last | Stop | Target | Days |")
        L.append("|---|---|---|---|---|---|---|---|")
        for p in pf["positions"]:
            L.append(f"| {p['symbol']} | {p['type']} | {p['qty']} | {p['entry_price']} | {p['last_price']} | "
                     f"{p['stop']} | {p['target']} | {p.get('days_held', 0)} |")
    else:
        L.append("None.")
    ai_err = sum(1 for e in ev if e["kind"] == "AI-ERROR")
    ai_skip = sum(1 for e in ev if e["kind"] == "AI-SKIP")
    no_plan = [e["detail"] for e in ev if e["kind"] == "NO-TRADE" and "plan" in e["detail"]]
    health = []
    if no_plan:
        health.append(f"⚠️ Morning plan problem: {no_plan[0]}. Open trades were still managed by their stops/targets.")
    if ai_err:
        health.append(f"⚠️ {ai_err} hourly AI check-in(s) failed today (API credits used up or provider down?). "
                      "The bot kept running on its coded rules only.")
    if ai_skip:
        health.append("ℹ️ Hourly AI check-ins are switched off (no API key configured).")
    L += ["", "## Health"] + (health or ["✅ Plan arrived on time, AI check-ins working."])
    L += ["", "## What happened (log)"]
    L += [f"- `{e['time'][11:16]}` **{e['kind']}** {e['detail']}" for e in ev] or ["- quiet day"]
    L += ["", "## Overall so far",
          f"Trades: {s_all['trades']} | Win rate: {s_all['win_rate']}% | Profit factor: {s_all['profit_factor']} | "
          f"Net: {s_all['net_pnl']:+,.0f} | Charges paid: {s_all['charges']:,.0f} | Max drawdown: {s_all['max_drawdown_pct']}%"]
    gl = go_live_check(pf, rules)
    L += ["", "## Go-live checklist"] + [f"- {'✅' if ok else '⬜'} {k}" for k, ok in gl["checks"].items()]
    return "\n".join(L)


def write_all(pf: dict, rules: dict, day: dt.date) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    text = daily(pf, rules, day)
    (REPORTS_DIR / f"{day.isoformat()}.md").write_text(text)
    (REPORTS_DIR / "latest.md").write_text(text)
    write_json(REPORTS_DIR / "dashboard.json", {
        "updated": day.isoformat(),
        "equity": equity(pf, rules),
        "pnl": period_pnl(pf, rules, day),
        "stats": stats(pf["closed"], pf["starting_capital"]),
        "by_strategy": by_strategy(pf["closed"], pf["starting_capital"]),
        "equity_history": pf["equity_history"],
        "open_positions": pf["positions"],
        "recent_trades": pf["closed"][-30:],
        "recent_events": pf["events"][-60:],
        "blocks": pf["blocks"],
        "go_live": go_live_check(pf, rules),
    })
