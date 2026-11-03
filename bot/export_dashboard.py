"""Exports small JSON files for the dashboard page (reports/dashboard_data/).

  python -m bot.export_dashboard
"""
from __future__ import annotations

import datetime as dt

from .config import REPORTS_DIR, ROOT, load_rules, read_json, write_json
from .portfolio import PF_PATH, load_control
from .report import go_live_check, stats
from .risk import equity, period_pnl
from .timeutil import now_ist

OUT = REPORTS_DIR / "dashboard_data"
RES = ROOT / "backtest" / "results"


def _status(pf, control, day):
    if control.get("paused"):
        return "Paused by owner"
    b = pf.get("blocks", {})
    if b.get("month_halted"):
        return "Stopped: monthly loss limit"
    if b.get("week_blocked_until") and day < dt.date.fromisoformat(b["week_blocked_until"]):
        return "Paused: weekly loss limit"
    if b.get("day_blocked") == day.isoformat():
        return "Paused today: daily loss limit"
    if b.get("no_trade_day") == day.isoformat():
        return "No trades today"
    return "Running"


def export() -> dict:
    rules = load_rules()
    day = now_ist().date()
    pf = read_json(PF_PATH)
    control = load_control()
    started = pf is not None and bool(pf.get("equity_history"))
    files = {}
    if pf:
        s = stats(pf["closed"], pf["starting_capital"])
        p = period_pnl(pf, rules, day)
        files["summary"] = [{
            "equity": equity(pf, rules), "day_pnl": p["day"], "week_pnl": p["week"], "month_pnl": p["month"],
            "net_pnl": s["net_pnl"], "trades": s["trades"], "win_rate": s["win_rate"],
            "profit_factor": s["profit_factor"], "max_dd": s["max_drawdown_pct"],
            "open_positions": len(pf["positions"]), "status": _status(pf, control, day) if started else "Not started yet",
            "days": len(pf["equity_history"]), "regime": pf.get("regime_today", ""),
            "updated": now_ist().strftime("%Y-%m-%d %H:%M")}]
        files["equity"] = [{"date": r["date"], "equity": r["equity"]} for r in pf["equity_history"]]
        files["trades"] = [{"trade_no": len(pf["closed"]) - k, "exit_date": t["exit_time"][:10], "symbol": t["symbol"], "strategy": t["strategy"],
                            "type": t["type"], "qty": t["qty"], "entry": t["entry_price"], "exit": t["exit_price"],
                            "net_pnl": t["net_pnl"], "reason": t["exit_reason"]}
                           for k, t in enumerate(reversed(pf["closed"][-200:]))]
        files["positions"] = [{"symbol": x["symbol"], "type": x["type"], "strategy": x["strategy"], "qty": x["qty"],
                               "entry": x["entry_price"], "last": x["last_price"], "stop": x["stop"],
                               "target": x["target"], "unrealized": round((x["last_price"] - x["entry_price"]) * x["qty"], 2),
                               "days": x.get("days_held", 0)} for x in pf["positions"]]
        files["events"] = [{"event_no": len(pf["events"]) - k, "time": e["time"][:16].replace("T", " "), "kind": e["kind"], "detail": e["detail"]}
                           for k, e in enumerate(reversed(pf["events"][-80:]))]
        files["golive"] = [{"check": k, "ok": "yes" if v else "no"} for k, v in go_live_check(pf, rules)["checks"].items()]
    else:
        cap = rules["capital"]["starting"]
        files["summary"] = [{"equity": cap, "day_pnl": 0, "week_pnl": 0, "month_pnl": 0, "net_pnl": 0, "trades": 0,
                             "win_rate": None, "profit_factor": None, "max_dd": 0, "open_positions": 0,
                             "status": "Not started yet", "days": 0, "regime": "",
                             "updated": now_ist().strftime("%Y-%m-%d %H:%M")}]
        files["equity"], files["trades"], files["positions"], files["events"] = [], [], [], []
        g = rules["go_live_criteria"]
        files["golive"] = [{"check": c, "ok": "no"} for c in (
            f"at least {g['min_paper_days']} trading days", f"at least {g['min_trades']} trades",
            "profit after charges", f"profit factor >= {g['min_profit_factor']}",
            f"max drawdown < {g['max_drawdown_pct']}%", "zero rule breaks")]

    # ---- momentum picks ----
    mst = read_json(ROOT / "state" / "momentum.json") or {}
    picks = set(mst.get("picks") or [])
    excl = mst.get("excluded") or {}
    files["picks"] = [{"rank": i + 1, "symbol": r["symbol"], "return_12m_pct": r["return_12m_pct"],
                       "above_200d": "yes" if r["above_200d"] else "no",
                       "decision": "BUY / HOLD" if r["symbol"] in picks else (f"AI excluded: {excl[r['symbol']]}" if r["symbol"] in excl else "—")}
                      for i, r in enumerate(mst.get("ranking") or [])]
    files["summary"][0]["next_rebalance"] = mst.get("next_rebalance", "")
    files["summary"][0]["ranking_date"] = mst.get("ranking_date", "")
    files["summary"][0]["market"] = ("" if "risk_on" not in mst else
                                     ("Nifty above 200-day avg: holding stocks" if mst["risk_on"] else "Nifty below 200-day avg: holding cash"))

    # ---- strategy testing ----
    variants, curves = [], []
    orig = read_json(RES / "results.json") or {}
    for name, v in orig.items():
        s = v["stats"]
        variants.append({"variant": f"Original: {name}", "round": "Round 1", "verdict": _v1(s), "trades": s["trades"],
                         "is_net": s["net_pnl"], "is_pf": s["profit_factor"], "oos_net": None, "oos_pf": None,
                         "oos_return": None, "oos_dd": s["max_drawdown_pct"]})
        if len(v["equity"]) > 30:
            for e in v["equity"][::5]:
                curves.append({"date": e["date"], "series": name, "equity": e["equity"]})
    research = read_json(RES / "research.json") or {}
    for name, v in (research.get("variants") or {}).items():
        i, o = v["in_sample"], v["out_of_sample"]
        variants.append({"variant": name, "round": "Round 2", "verdict": v["verdict"], "trades": v["trades"],
                         "is_net": i["net_pnl"], "is_pf": i["profit_factor"], "oos_net": o["net_pnl"],
                         "oos_pf": o["profit_factor"], "oos_return": o["return_pct"], "oos_dd": o["equity_dd_pct"]})
        for e in v["equity"]:
            curves.append({"date": e["date"], "series": name.split(":")[0], "equity": e["equity"]})
    for b in research.get("nifty_benchmark") or []:
        curves.append({"date": b["date"], "series": "Nifty 50 (just holding)", "equity": b["value"]})
    files["variants"] = variants
    files["variant_equity"] = curves

    OUT.mkdir(parents=True, exist_ok=True)
    for k, rows in files.items():
        write_json(OUT / f"{k}.json", rows)
        _write_csv(OUT / f"{k}.csv", rows, COLUMNS[k])
    return files


COLUMNS = {
    "summary": ["equity", "day_pnl", "week_pnl", "month_pnl", "net_pnl", "trades", "win_rate", "profit_factor",
                "max_dd", "open_positions", "status", "days", "regime", "updated", "next_rebalance", "ranking_date", "market"],
    "picks": ["rank", "symbol", "return_12m_pct", "above_200d", "decision"],
    "equity": ["date", "equity"],
    "trades": ["trade_no", "exit_date", "symbol", "strategy", "type", "qty", "entry", "exit", "net_pnl", "reason"],
    "positions": ["symbol", "type", "strategy", "qty", "entry", "last", "stop", "target", "unrealized", "days"],
    "events": ["event_no", "time", "kind", "detail"],
    "golive": ["check", "ok"],
    "variants": ["variant", "round", "verdict", "trades", "is_net", "is_pf", "oos_net", "oos_pf", "oos_return", "oos_dd"],
    "variant_equity": ["date", "series", "equity"],
}


def _write_csv(path, rows, cols):
    """CSV keeps its header even with no rows, so empty tables still load on the dashboard."""
    import csv
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})


def _v1(s):
    if s["trades"] < 30:
        return "NOT ENOUGH TRADES"
    return "PASS" if s["net_pnl"] > 0 and (s["profit_factor"] or 0) >= 1.2 else ("WEAK" if s["net_pnl"] > 0 else "FAIL")


if __name__ == "__main__":
    f = export()
    print({k: len(v) for k, v in f.items()})
