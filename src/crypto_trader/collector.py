from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from statistics import mean
from typing import Iterable

from .config import Settings, get_settings
from .db import connect, init_db
from .indicators import rsi as indicator_rsi

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
}
SYMBOL_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "ether", "eth"],
    "SOLUSDT": ["solana", "sol"],
    "XRPUSDT": ["xrp", "ripple"],
    "DOGEUSDT": ["dogecoin", "doge"],
}


@dataclass(frozen=True)
class Candle:
    exchange: str
    symbol: str
    interval: str
    open_time_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    last_close: float
    change_1h_pct: float | None
    change_24h_pct: float | None
    sma_20: float | None
    rsi_14: float | None
    trend: str
    notes: str


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def http_get(url: str, settings: Settings, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": settings.user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_binance_klines(symbol: str, settings: Settings) -> list[Candle]:
    params = urllib.parse.urlencode({"symbol": symbol, "interval": settings.interval, "limit": settings.candle_limit})
    payload = http_get(f"{BINANCE_KLINES_URL}?{params}", settings)
    rows = json.loads(payload.decode("utf-8"))
    candles: list[Candle] = []
    for row in rows:
        open_time = datetime.fromtimestamp(row[0] / 1000, tz=UTC).replace(microsecond=0)
        candles.append(
            Candle(
                exchange="binance",
                symbol=symbol,
                interval=settings.interval,
                open_time_utc=open_time.isoformat().replace("+00:00", "Z"),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        )
    return candles


def upsert_candles(conn: sqlite3.Connection, candles: Iterable[Candle]) -> int:
    rows = [c.__dict__ for c in candles]
    conn.executemany(
        """
        INSERT OR REPLACE INTO candles
        (exchange, symbol, interval, open_time_utc, open, high, low, close, volume)
        VALUES (:exchange, :symbol, :interval, :open_time_utc, :open, :high, :low, :close, :volume)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100


def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    return indicator_rsi(closes, period)


def analyse_candles(symbol: str, candles: list[Candle]) -> MarketSnapshot:
    closes = [c.close for c in candles]
    last_close = closes[-1]
    change_1h = pct_change(last_close, closes[-2]) if len(closes) >= 2 else None
    change_24h = pct_change(last_close, closes[-25]) if len(closes) >= 25 else None
    sma_20 = mean(closes[-20:]) if len(closes) >= 20 else None
    rsi_14 = calculate_rsi(closes)

    trend_parts: list[str] = []
    if sma_20 is not None:
        trend_parts.append("above_sma20" if last_close >= sma_20 else "below_sma20")
    if change_24h is not None:
        if change_24h >= 3:
            trend_parts.append("strong_24h_up")
        elif change_24h <= -3:
            trend_parts.append("strong_24h_down")
        else:
            trend_parts.append("range_24h")
    if rsi_14 is not None:
        if rsi_14 >= 70:
            trend_parts.append("rsi_hot")
        elif rsi_14 <= 30:
            trend_parts.append("rsi_cold")
        else:
            trend_parts.append("rsi_neutral")

    if any(p == "strong_24h_up" for p in trend_parts) and any(p == "above_sma20" for p in trend_parts):
        trend = "bullish"
    elif any(p == "strong_24h_down" for p in trend_parts) and any(p == "below_sma20" for p in trend_parts):
        trend = "bearish"
    else:
        trend = "mixed"

    notes = ", ".join(trend_parts) if trend_parts else "insufficient_data"
    return MarketSnapshot(symbol, last_close, change_1h, change_24h, sma_20, rsi_14, trend, notes)


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return None


def extract_text(element: ET.Element, tag: str) -> str:
    found = element.find(tag)
    if found is None or found.text is None:
        return ""
    return html.unescape(found.text.strip())


def match_symbols(title: str, summary: str, symbols: tuple[str, ...]) -> str:
    text = f"{title} {summary}".lower()
    matched = []
    for symbol in symbols:
        keywords = SYMBOL_KEYWORDS.get(symbol, [symbol.lower().replace("usdt", "")])
        if any(keyword in text for keyword in keywords):
            matched.append(symbol)
    return ",".join(matched)


def fetch_news(settings: Settings, collected_at: str) -> list[dict[str, str | None]]:
    items: list[dict[str, str | None]] = []
    for source, url in NEWS_FEEDS.items():
        try:
            payload = http_get(url, settings, timeout=30)
            root = ET.fromstring(payload)
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            items.append(
                {
                    "source": source,
                    "title": f"FEED_ERROR: {exc}",
                    "link": f"feed-error:{source}:{collected_at}",
                    "published_utc": collected_at,
                    "summary": "Feed could not be fetched or parsed.",
                    "first_seen_utc": collected_at,
                    "matched_symbols": "",
                }
            )
            continue

        channel = root.find("channel")
        feed_items = channel.findall("item") if channel is not None else root.findall("{http://www.w3.org/2005/Atom}entry")
        for item in feed_items[: settings.max_news_items_per_source]:
            title = extract_text(item, "title")
            link = extract_text(item, "link")
            if not link:
                atom_link = item.find("{http://www.w3.org/2005/Atom}link")
                link = atom_link.attrib.get("href", "") if atom_link is not None else ""
            summary = extract_text(item, "description") or extract_text(item, "summary")
            published = parse_date(extract_text(item, "pubDate") or extract_text(item, "published") or extract_text(item, "updated"))
            if not title or not link:
                continue
            items.append(
                {
                    "source": source,
                    "title": title,
                    "link": link,
                    "published_utc": published,
                    "summary": summary[:1000],
                    "first_seen_utc": collected_at,
                    "matched_symbols": match_symbols(title, summary, settings.symbols),
                }
            )
    return items


def upsert_news(conn: sqlite3.Connection, news_items: list[dict[str, str | None]]) -> int:
    conn.executemany(
        """
        INSERT OR IGNORE INTO news_items
        (source, title, link, published_utc, summary, first_seen_utc, matched_symbols)
        VALUES (:source, :title, :link, :published_utc, :summary, :first_seen_utc, :matched_symbols)
        """,
        news_items,
    )
    conn.commit()
    return conn.total_changes


def save_snapshot(conn: sqlite3.Connection, collected_at: str, settings: Settings, snapshot: MarketSnapshot) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO market_snapshots
        (collected_at_utc, exchange, symbol, interval, last_close, change_1h_pct, change_24h_pct, sma_20, rsi_14, trend, notes)
        VALUES (?, 'binance', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collected_at,
            snapshot.symbol,
            settings.interval,
            snapshot.last_close,
            snapshot.change_1h_pct,
            snapshot.change_24h_pct,
            snapshot.sma_20,
            snapshot.rsi_14,
            snapshot.trend,
            snapshot.notes,
        ),
    )
    conn.commit()


def market_bias(snapshots: list[MarketSnapshot]) -> str:
    if not snapshots:
        return "unknown"
    score = 0
    for snapshot in snapshots:
        score += 1 if snapshot.trend == "bullish" else -1 if snapshot.trend == "bearish" else 0
        if snapshot.change_24h_pct and snapshot.change_24h_pct > 0:
            score += 0.25
        elif snapshot.change_24h_pct and snapshot.change_24h_pct < 0:
            score -= 0.25
    if score >= 1.5:
        return "risk-on / bullish"
    if score <= -1.5:
        return "risk-off / bearish"
    return "mixed / wait for confirmation"


def save_run_summary(conn: sqlite3.Connection, collected_at: str, snapshots: list[MarketSnapshot], candles_count: int, news_count: int) -> dict[str, object]:
    bias = market_bias(snapshots)
    lines = [f"Market bias: {bias}"]
    for snapshot in snapshots:
        change_24h = "n/a" if snapshot.change_24h_pct is None else f"{snapshot.change_24h_pct:.2f}%"
        rsi = "n/a" if snapshot.rsi_14 is None else f"{snapshot.rsi_14:.1f}"
        lines.append(f"{snapshot.symbol}: {snapshot.trend}, 24h {change_24h}, RSI {rsi}, {snapshot.notes}")
    summary = "\n".join(lines)
    conn.execute(
        """
        INSERT OR REPLACE INTO run_summaries
        (collected_at_utc, symbols_checked, candles_upserted, news_upserted, market_bias, summary)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (collected_at, len(snapshots), candles_count, news_count, bias, summary),
    )
    conn.commit()
    return {
        "collected_at_utc": collected_at,
        "symbols_checked": len(snapshots),
        "candles_upserted": candles_count,
        "news_upserted": news_count,
        "market_bias": bias,
        "summary": summary,
    }


def collect_once(settings: Settings) -> dict[str, object]:
    collected_at = utc_now_iso()
    conn = connect(settings.database_path)
    init_db(conn)

    snapshots: list[MarketSnapshot] = []
    candles_count = 0
    for symbol in settings.symbols:
        candles = fetch_binance_klines(symbol, settings)
        candles_count += upsert_candles(conn, candles)
        snapshot = analyse_candles(symbol, candles)
        save_snapshot(conn, collected_at, settings, snapshot)
        snapshots.append(snapshot)

    before_changes = conn.total_changes
    news = fetch_news(settings, collected_at)
    upsert_news(conn, news)
    news_count = conn.total_changes - before_changes

    result = save_run_summary(conn, collected_at, snapshots, candles_count, news_count)
    conn.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect centralized crypto market/news data")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        result = collect_once(settings)
    except Exception as exc:  # n8n should surface a clear failure.
        print(f"crypto-monitor failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
