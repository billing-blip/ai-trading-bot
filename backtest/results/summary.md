# Backtest results

_Generated 2026-11-03 16:32 IST_

Same rules as the live bot: ₹500 max loss per trade (incl. charges & slippage), max 4 swing trades, ₹2,000 total open risk, daily/weekly/monthly loss limits, realistic brokerage + taxes. Uses today's Nifty 50 list (survivorship bias → real results would be somewhat worse).

## Swing — no filter

**Verdict: FAIL — drop or rework**

| Trades | Win rate | Net P&L | Profit factor | Avg win | Avg loss | Max drawdown | Charges |
|---|---|---|---|---|---|---|---|
| 698 | 32.2% | ₹-56,277 | 0.65 | ₹471.98 | ₹-343.49 | 58.6% | ₹32,758 |

By strategy:

| Strategy | Trades | Win rate | Net | PF | Verdict |
|---|---|---|---|---|---|
| breakout | 328 | 34.8% | ₹-25,437 | 0.67 | FAIL — drop or rework |
| trend_pullback | 370 | 30.0% | ₹-30,840 | 0.64 | FAIL — drop or rework |

By year:

| Year | Trades | Net | PF | Max DD |
|---|---|---|---|---|
| 2022 | 138 | ₹-16,068 | 0.56 | 16.07% |
| 2023 | 146 | ₹4,301 | 1.16 | 7.35% |
| 2024 | 149 | ₹-17,123 | 0.55 | 17.61% |
| 2025 | 147 | ₹-12,385 | 0.63 | 14.15% |
| 2026 | 118 | ₹-15,002 | 0.44 | 18.16% |

Monthly loss-limit full stops triggered: 1

## Swing — trend filter (AI proxy)

**Verdict: FAIL — drop or rework**

| Trades | Win rate | Net P&L | Profit factor | Avg win | Avg loss | Max drawdown | Charges |
|---|---|---|---|---|---|---|---|
| 464 | 34.1% | ₹-31,278 | 0.7 | ₹460.85 | ₹-340.17 | 32.25% | ₹22,371 |

By strategy:

| Strategy | Trades | Win rate | Net | PF | Verdict |
|---|---|---|---|---|---|
| breakout | 277 | 37.9% | ₹-13,368 | 0.78 | FAIL — drop or rework |
| trend_pullback | 187 | 28.3% | ₹-17,910 | 0.58 | FAIL — drop or rework |

By year:

| Year | Trades | Net | PF | Max DD |
|---|---|---|---|---|
| 2022 | 95 | ₹-9,680 | 0.62 | 10.95% |
| 2023 | 94 | ₹-370 | 0.98 | 7.09% |
| 2024 | 123 | ₹-8,799 | 0.7 | 9.58% |
| 2025 | 96 | ₹-9,536 | 0.57 | 11.34% |
| 2026 | 56 | ₹-2,893 | 0.71 | 4.88% |

Monthly loss-limit full stops triggered: 1

## Intraday ORB — last ~60 days (trend days only)

**Verdict: NOT ENOUGH TRADES to judge**

| Trades | Win rate | Net P&L | Profit factor | Avg win | Avg loss | Max drawdown | Charges |
|---|---|---|---|---|---|---|---|
| 4 | 25.0% | ₹-554 | 0.11 | ₹68.22 | ₹-207.52 | 0.62% | ₹81 |
