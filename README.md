# Crypto Trader Dashboard

Goal: build a safe crypto trading assistant with paper trading first, full observability, and manual approval before any live trade.

## Current configuration

- Watchlist: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT
- Quote currency: USDT for trading logic; CAD can be added later as a display/reporting layer
- Paper balance: 1000 USDT
- Risk profile: conservative
- Scan interval: 15m
- Portfolio tracking: manual holdings later, not required for the first build
- Discord crypto channel: `1506657476827811993`

## Current stance

This project starts in **research + paper trading mode**. No real funds. No exchange API trading permissions until the strategy, dashboard, logs, and risk controls are proven.

## Core principles

1. Capital protection first.
2. Every signal must be logged and explainable.
3. Paper trading before live trading.
4. Manual approval before first live trade.
5. Hard limits: max position size, daily loss cap, stop-loss, kill switch.
6. No leverage by default.
7. No meme-coin roulette unless explicitly enabled. The casino already has enough customers.

## Planned stack

- n8n: scheduled workflows, exchange data polling, alerting, approval flows
- Python: strategy engine, backtesting, risk rules
- SQLite or Postgres: trades, signals, candles, balances, audit logs
- Web dashboard: live status, positions, signals, P/L, risk state, explanations
- Discord: summaries, alerts, manual approval prompts

## Phases

### Phase 1 — Research + architecture

- Pick exchange/data provider.
- Define supported markets.
- Define trading style: intraday, scalp, swing, or signal-only.
- Build glossary and strategy notes.
- Store OHLCV candles and indicators.

### Phase 2 — Paper trading MVP

- Pull market data on schedule.
- Generate signals.
- Simulate buys/sells with fees/slippage.
- Save every decision to database.
- Dashboard shows what would have happened.

### Phase 3 — Backtesting

- Run strategies against historical data.
- Track win rate, profit factor, drawdown, Sharpe-ish metrics, max consecutive losses.
- Reject strategies that only look good because of curve fitting.

### Phase 4 — Human-reviewed live mode

- Exchange API keys use read-only first.
- Then trading permission only, no withdrawal permission.
- n8n sends proposed trade to Discord.
- User approves/rejects.
- Bot executes only approved orders.

### Phase 5 — Limited automation

- Small position size.
- Daily loss cap.
- Trade frequency cap.
- Kill switch.
- Automatic halt on API/data anomalies.

## First decisions needed

1. Exchange: Binance, Kraken, Coinbase, KuCoin, etc.
2. Markets: BTC/USDT, ETH/USDT, SOL/USDT, etc.
3. Mode: paper trading only first, or read-only portfolio tracking too.
4. Dashboard hosting: local only, LAN, or public behind auth.
5. Database: SQLite for simple/local, Postgres if you want more robust.

## Safety notes

- This is not financial advice.
- Crypto is volatile and mostly allergic to dignity.
- Live trading starts only after explicit confirmation and scoped API keys.
- API keys must never include withdrawal permission.
