# AI Trading Bot — Rulebook (v1, paper trading)

Approved 3 Nov 2026. The numbers live in `config/rules.yaml`; the code enforces them and the
AI cannot change them.

## 1. Money and risk limits
| Rule | Limit |
|---|---|
| Starting capital (paper) | ₹1,00,000 |
| Swing / intraday capital | up to ₹70,000 / up to ₹30,000 |
| **Max loss per trade** | **₹500 (0.5%)** including charges |
| Max size of one position | ₹25,000 |
| Max open swing trades | 4 |
| Max intraday trades per day | 2 |
| Max total open risk | ₹2,000 |
| Daily loss limit | ₹1,000 → no new trades rest of day |
| Weekly loss limit | ₹2,000 → no new trades until next Monday |
| Monthly loss limit | ₹5,000 → full stop until reviewed |
| Leverage, options, F&O | Never |

Trade size is calculated so that hitting the stop-loss loses ₹500 at most. If a stock opens
above the planned entry, the quantity is reduced so the ₹500 cap still holds.

## 2. Every trade
- No stop-loss → no trade. Stops only move closer, never further away.
- Target ≥ 2× the risk.
- Stop moves to breakeven once the trade is +1R in profit.
- Never add to a losing trade.
- **Buy-only in v1.** Intraday short selling may come in v2 after testing.
- Swing: exit after 10 trading days if neither stop nor target is hit.
- Intraday: everything closed at 3:15 PM.

## 3. Daily timeline (IST)
| Time | What happens |
|---|---|
| 08:00 | Bot downloads prices, finds valid setups (snapshot) |
| ~08:30 | Claude reads news + snapshot, writes the plan |
| 09:00 | Plan deadline — **no plan = no trades today** |
| 09:10 | Plan checked and turned into pending orders |
| 09:15 | Gap check on the first candle: stock >2% past entry → cancelled; Nifty >1.5% gap → no-trade day |
| 09:15–09:30 | No entries |
| 09:50 → 15:20 | Hourly checks: fills, stops, targets, ORB setup, AI risk check-in |
| 14:30 | No new intraday entries |
| 15:15 | Intraday positions closed |
| 16:00 | End of day: reports, limits, audit |

No trades on market holidays, Budget day, RBI policy days, election results days, or in a
stock within 2 days of its results.

## 4. Strategy menu (the AI picks; it never invents)
| # | Strategy | Type | Idea |
|---|---|---|---|
| A | Trend pullback | Swing | Uptrend stock dips to its 20-day average and bounces |
| B | Breakout | Swing | Closes above its 20-day high on 1.5× volume |
| C | Opening range breakout | Intraday | On trending-up days, buy above the first 30-minute high |
| D | No trade | — | Choppy / risky / unclear |

Each strategy is backtested on past data first; failing strategies are switched off.

## 5. What the AI may do
**Morning:** choose the regime (trending_up / sideways / falling / high_risk), pick up to 5
stocks from the code-built candidate list, remove stocks because of news/results, give reasons.
**Hourly check-ins:** only reduce risk — exit early, tighten a stop, cancel a pending entry,
or stop trading for the day. Every AI reply is checked by code; anything else is rejected.
**When unsure, or data is missing → no trade.**

## 6. Reports
Daily report + dashboard data after the close; every decision logged with its reason.

## 7. Going live
Paper trade ≥ 2 months and ≥ 40 trades. Go live only if: profitable after charges,
max drawdown < 6%, profit factor ≥ 1.3, zero rule breaks. First live month at half size
(₹250 risk per trade).
