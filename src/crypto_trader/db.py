from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS candles (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time_utc TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (exchange, symbol, interval, open_time_utc)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    collected_at_utc TEXT NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    last_close REAL NOT NULL,
    change_1h_pct REAL,
    change_24h_pct REAL,
    sma_20 REAL,
    rsi_14 REAL,
    trend TEXT NOT NULL,
    notes TEXT NOT NULL,
    PRIMARY KEY (collected_at_utc, exchange, symbol, interval)
);

CREATE TABLE IF NOT EXISTS news_items (
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL PRIMARY KEY,
    published_utc TEXT,
    summary TEXT,
    first_seen_utc TEXT NOT NULL,
    matched_symbols TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_summaries (
    collected_at_utc TEXT PRIMARY KEY,
    symbols_checked INTEGER NOT NULL,
    candles_upserted INTEGER NOT NULL,
    news_upserted INTEGER NOT NULL,
    market_bias TEXT NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_signals (
    generated_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    price REAL NOT NULL,
    reason TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    PRIMARY KEY (generated_at_utc, symbol, interval)
);

CREATE TABLE IF NOT EXISTS paper_orders (
    generated_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    fee_usdt REAL NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (generated_at_utc, symbol, side)
);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
