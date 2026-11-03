# AI Trading Bot (paper trading)

A fully automatic **paper-trading** bot for Nifty 50 stocks. No real money is used.

- **Claude** (morning, via a scheduled task) reads the news and the bot's market snapshot and
  picks which pre-checked setups to trade today — or none.
- **The bot** (GitHub Actions) checks prices through the day, simulates fills, stop-losses and
  targets exactly from 5-minute candles, and enforces every limit in [RULEBOOK.md](RULEBOOK.md).
- **A cheap AI model** checks open trades every hour and may only *reduce* risk.

Setup steps: [SETUP.md](SETUP.md).

## Where things are
| Folder / file | What it is |
|---|---|
| `config/rules.yaml` | All limits and strategy settings (the rulebook in numbers) |
| `config/calendar.yaml` | Market holidays, no-trade event days, blocked stocks |
| `bot/` | The bot's code |
| `planner/MORNING_PROMPT.md` | Instructions Claude follows every morning |
| `data/snapshots/` | Each morning's market snapshot + valid setups |
| `plans/` | Each morning's AI plan |
| `state/portfolio.json` | The paper account: cash, positions, history |
| `state/control.json` | Pause switch (`"paused": true` stops new trades) |
| `reports/latest.md` | Today's report (also one file per day) |
| `reports/dashboard.json` | Data for the dashboard |
| `backtest/results/summary.md` | Backtest results |

## Commands (run automatically — listed for reference)
```
python -m bot.cli snapshot | activate | tick | eod | status | pause | resume
python -m backtest.run_backtest
python -m pytest -q
```
