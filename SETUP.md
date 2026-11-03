# Setup guide (one time, ~20 minutes)

You only click buttons on websites — no coding.

## Step 1 — Let the bot save its work
GitHub → your repository **ai-trading-bot** → **Settings** → **Actions** → **General** →
scroll to **Workflow permissions** → choose **Read and write permissions** → **Save**.

## Step 2 — Add the AI key (kept secret, encrypted by GitHub)
Repository → **Settings** → **Secrets and variables** → **Actions**.

On the **Secrets** tab, click **New repository secret** twice:

| Name | Value |
|---|---|
| `LLM_API_KEY` | your API key (a fresh one — delete the one shared in chat) |
| `LLM_BASE_URL` | your API website's address (Claude will tell you the exact value) |

On the **Variables** tab, click **New repository variable**:

| Name | Value |
|---|---|
| `LLM_MODEL` | the exact model name on your API website (Claude will tell you) |

## Step 3 — Run the backtest
Repository → **Actions** → **backtest** (left side) → **Run workflow** → **Run workflow**.
Takes ~10 minutes. Then tell Claude "backtest done" — it will read the results with you.

## Step 4 — A key for the alarm clock (cron-job.org)
GitHub (top-right photo) → **Settings** → **Developer settings** → **Personal access tokens** →
**Fine-grained tokens** → **Generate new token**:
- Name: `cron-job`  •  Expiration: 90 days (set a reminder to renew)
- Repository access: **Only select repositories** → `ai-trading-bot`
- Permissions → Repository permissions → **Actions: Read and write**
- **Generate token** and copy it. Paste it ONLY into cron-job.org (next step), never into chat.

## Step 5 — Set the alarms on cron-job.org
In cron-job.org: **Settings** → time zone **Asia/Kolkata**. Then create one cron job per row
(Create cronjob → "Advanced" tab for the method, headers and body):

- **URL:** `https://api.github.com/repos/billing-blip/ai-trading-bot/actions/workflows/bot.yml/dispatches`
- **Method:** POST
- **Headers:**
  - `Authorization` → `Bearer YOUR_TOKEN`
  - `Accept` → `application/vnd.github+json`
  - `X-GitHub-Api-Version` → `2022-11-28`
- **Days:** Monday–Friday only

| Job title | Time (IST) | Request body |
|---|---|---|
| bot snapshot | 08:00 | `{"ref":"main","inputs":{"job":"snapshot"}}` |
| bot activate | 09:10 | `{"ref":"main","inputs":{"job":"activate"}}` |
| bot tick 1 | 09:50 | `{"ref":"main","inputs":{"job":"tick"}}` |
| bot tick 2 | 10:50 | `{"ref":"main","inputs":{"job":"tick"}}` |
| bot tick 3 | 11:50 | `{"ref":"main","inputs":{"job":"tick"}}` |
| bot tick 4 | 12:50 | `{"ref":"main","inputs":{"job":"tick"}}` |
| bot tick 5 | 13:50 | `{"ref":"main","inputs":{"job":"tick"}}` |
| bot tick 6 | 14:35 | `{"ref":"main","inputs":{"job":"tick"}}` |
| bot tick 7 | 15:20 | `{"ref":"main","inputs":{"job":"tick"}}` |
| bot eod | 16:00 | `{"ref":"main","inputs":{"job":"eod"}}` |

Test one: click **Run now** on "bot snapshot", then check GitHub → **Actions** — a run should
appear within a minute. (Success shows as a green tick; cron-job.org shows "204".)

## Step 6 — Morning planner
Claude sets this up as a scheduled task in your Claude account (weekdays 08:30 IST).

## Pausing
Tell Claude "pause trading" — or edit `state/control.json` on GitHub and set `"paused": true`.
