# Crypto Trading Assistant Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a safe cryptocurrency trading assistant with research, paper trading, dashboard visibility, Discord/n8n automation, and optional manually-approved live trading later.

**Architecture:** n8n handles orchestration, schedules, notifications, and approvals. Python handles market data, strategy logic, backtesting, and risk controls. A web dashboard reads from the database and shows signals, decisions, paper trades, balances, P/L, and audit logs.

**Tech Stack:** Python 3, FastAPI, SQLite first, optional Postgres later, n8n, ccxt, pandas, lightweight frontend HTML/JS or React later if needed.

---

## Non-negotiable safety rules

1. Start with paper trading only.
2. No live exchange trading until explicitly approved.
3. Exchange API keys must never include withdrawal permission.
4. Every signal and simulated/live order must be saved with timestamp, inputs, decision, and risk result.
5. Manual approval is required for first live phase.
6. Use daily loss caps, max position size, stop-loss, cooldowns, and kill switch before live trading.

---

## Task 1: Create project skeleton

**Objective:** Create a clean Python/web project structure.

**Files:**
- Create: `pyproject.toml`
- Create: `src/crypto_trader/__init__.py`
- Create: `src/crypto_trader/config.py`
- Create: `src/crypto_trader/db.py`
- Create: `src/crypto_trader/main.py`
- Create: `tests/test_smoke.py`

**Verification:**
- `python -m pytest -q` passes.
- `python -m crypto_trader.main` starts or prints app status.

---

## Task 2: Add configuration model

**Objective:** Load safe defaults from environment variables without secrets in code.

**Config fields:**
- `APP_ENV`
- `DATABASE_URL`
- `EXCHANGE_ID`
- `TRADING_MODE` with allowed values: `research`, `paper`, `manual_live`, `auto_live`
- `MAX_POSITION_PCT`
- `DAILY_LOSS_LIMIT_PCT`

**Verification:**
- Invalid `TRADING_MODE=auto_live` without explicit enable flag fails closed.

---

## Task 3: Add database schema

**Objective:** Store candles, signals, paper orders, positions, balances, and audit logs.

**Tables:**
- `candles`
- `signals`
- `paper_orders`
- `positions`
- `risk_events`
- `audit_log`

**Verification:**
- Migration/init command creates database.
- Tests confirm required tables exist.

---

## Task 4: Add market data ingestion

**Objective:** Fetch OHLCV candles through ccxt and save them.

**Rules:**
- Start with public endpoints only.
- No API keys required.
- Normalize timestamps as UTC ISO strings.
- Store symbol, timeframe, open, high, low, close, volume.

**Verification:**
- Fetch BTC/USDT candles from selected exchange.
- Read back saved rows.

---

## Task 5: Add indicator engine

**Objective:** Compute basic indicators for strategy use.

**Indicators:**
- EMA fast/slow
- RSI
- ATR
- Volume moving average

**Verification:**
- Unit tests use fixed sample data and assert stable outputs.

---

## Task 6: Add first strategy

**Objective:** Implement a conservative baseline strategy for paper testing.

**Initial strategy:**
- Trend filter: price above slow EMA.
- Entry: fast EMA crosses above slow EMA and RSI not overbought.
- Exit: stop-loss, take-profit, trend break, or RSI extreme.

**Verification:**
- Strategy returns `BUY`, `SELL`, or `HOLD` with explanation.
- Every decision includes indicator values used.

---

## Task 7: Add paper trading engine

**Objective:** Simulate trades with fees and slippage.

**Rules:**
- Configurable starting balance.
- Configurable fee rate.
- Configurable slippage.
- Position sizing based on risk settings.

**Verification:**
- Known price sequence produces expected simulated balance and position state.

---

## Task 8: Add risk engine

**Objective:** Block unsafe trades before paper/live execution.

**Rules:**
- Max position percentage.
- Max daily loss percentage.
- Max trades per day.
- Cooldown after loss.
- Kill switch flag.

**Verification:**
- Tests prove risky trades are blocked with clear reasons.

---

## Task 9: Add FastAPI dashboard API

**Objective:** Expose status endpoints for the dashboard.

**Endpoints:**
- `GET /health`
- `GET /api/status`
- `GET /api/signals`
- `GET /api/paper-orders`
- `GET /api/positions`
- `GET /api/risk-events`

**Verification:**
- API starts locally.
- Endpoints return JSON.

---

## Task 10: Add web dashboard

**Objective:** Build a simple readable UI before getting fancy.

**Views:**
- Current mode and kill switch state.
- Watched markets.
- Latest signals with explanation.
- Open paper positions.
- Paper P/L.
- Risk blocks.
- Trade/audit timeline.

**Verification:**
- Dashboard loads in browser.
- No secrets displayed.

---

## Task 11: Add n8n workflow integration

**Objective:** Let n8n trigger market scans and send Discord summaries.

**Workflow pieces:**
- Schedule Trigger.
- HTTP Request to local `/api/scan` or Python CLI webhook.
- Discord message with summary.
- Persist workflow cursor/state if polling external data.

**Verification:**
- Manual run produces a Discord summary.
- No duplicate spam on repeated runs.

---

## Task 12: Add manual approval path

**Objective:** Prepare for future live trading without enabling it.

**Flow:**
- Strategy proposes trade.
- Risk engine validates.
- n8n sends Discord approval message.
- Approved decision is logged.
- Live execution remains disabled until separate implementation.

**Verification:**
- Approval/rejection is recorded.
- No exchange order is placed.

---

## Task 13: Add exchange account read-only tracking

**Objective:** Read balances/positions without trading.

**Rules:**
- Read-only API key only.
- No withdrawal permission.
- Mask key metadata in logs.

**Verification:**
- Balance endpoint works.
- Logs do not reveal secrets.

---

## Task 14: Add live trading execution gate

**Objective:** Add live execution code behind multiple locks.

**Required gates:**
- `TRADING_MODE=manual_live`
- explicit `ENABLE_LIVE_TRADING=true`
- exchange key has trade permission but no withdrawal permission
- risk engine passes
- user approval exists

**Verification:**
- Default config refuses live trade.
- Missing approval refuses live trade.
- Kill switch refuses live trade.

---

## Open questions

1. Which exchange should be supported first?
2. Which trading pairs should be watched first?
3. Should dashboard be LAN-only or public behind auth?
4. Should database start SQLite or Postgres?
5. Do you want Discord approvals, dashboard approvals, or both?
