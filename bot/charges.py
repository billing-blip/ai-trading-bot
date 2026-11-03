"""Estimated brokerage + taxes, so paper profits are realistic (Zerodha-style rates)."""
from __future__ import annotations


def round_trip(buy_value: float, sell_value: float, intraday: bool, c: dict) -> float:
    """Total charges for one buy + one sell."""
    turnover = buy_value + sell_value
    if intraday:
        brokerage = min(c["intraday_brokerage_cap"], buy_value * c["intraday_brokerage_pct"] / 100) + min(
            c["intraday_brokerage_cap"], sell_value * c["intraday_brokerage_pct"] / 100
        )
        stt = sell_value * c["stt_intraday_sell_pct"] / 100
        stamp = buy_value * c["stamp_intraday_buy_pct"] / 100
        dp = 0.0
    else:
        brokerage = c["delivery_brokerage"] * 2
        stt = turnover * c["stt_delivery_pct"] / 100
        stamp = buy_value * c["stamp_delivery_buy_pct"] / 100
        dp = c["dp_charge_per_sell"]
    txn = turnover * c["exchange_txn_pct"] / 100
    sebi = turnover * c["sebi_per_crore"] / 1e7
    gst = (brokerage + txn + sebi) * c["gst_pct"] / 100
    return round(brokerage + stt + stamp + dp + txn + sebi + gst, 2)


def slip(price: float, side: str, c: dict) -> float:
    """Apply assumed slippage: buys fill a bit higher, sells a bit lower."""
    s = c.get("slippage_pct", 0) / 100
    return price * (1 + s) if side == "buy" else price * (1 - s)
