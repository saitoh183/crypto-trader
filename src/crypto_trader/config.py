from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
REPORT_DIR = REPO_ROOT / "reports"


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path(os.getenv("CRYPTO_MONITOR_DB", DATA_DIR / "crypto_monitor.sqlite3"))
    report_path: Path = Path(os.getenv("CRYPTO_MONITOR_REPORT", REPORT_DIR / "latest.html"))
    symbols: tuple[str, ...] = tuple(
        s.strip().upper()
        for s in os.getenv("CRYPTO_MONITOR_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
        if s.strip()
    )
    interval: str = os.getenv("CRYPTO_MONITOR_INTERVAL", "1h")
    candle_limit: int = int(os.getenv("CRYPTO_MONITOR_CANDLE_LIMIT", "120"))
    max_news_items_per_source: int = int(os.getenv("CRYPTO_MONITOR_NEWS_LIMIT", "15"))
    user_agent: str = os.getenv(
        "CRYPTO_MONITOR_USER_AGENT",
        "Mozilla/5.0 (compatible; CryptoMonitor/0.1; +https://saitohsmedia.com)",
    )


def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
