from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
REPORT_DIR = REPO_ROOT / "reports"
DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT"


def env_path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default)))


def env_symbols(name: str = "CRYPTO_MONITOR_SYMBOLS", default: str = DEFAULT_SYMBOLS) -> tuple[str, ...]:
    return tuple(s.strip().upper() for s in os.getenv(name, default).split(",") if s.strip())


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    database_path: Path = field(default_factory=lambda: env_path("CRYPTO_MONITOR_DB", DATA_DIR / "crypto_monitor.sqlite3"))
    report_path: Path = field(default_factory=lambda: env_path("CRYPTO_MONITOR_REPORT", REPORT_DIR / "latest.html"))
    symbols: tuple[str, ...] = field(default_factory=env_symbols)
    interval: str = field(default_factory=lambda: os.getenv("CRYPTO_MONITOR_INTERVAL", "15m"))
    candle_limit: int = field(default_factory=lambda: env_int("CRYPTO_MONITOR_CANDLE_LIMIT", 120))
    max_news_items_per_source: int = field(default_factory=lambda: env_int("CRYPTO_MONITOR_NEWS_LIMIT", 15))
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "CRYPTO_MONITOR_USER_AGENT",
            "Mozilla/5.0 (compatible; CryptoMonitor/0.1; +https://saitohsmedia.com)",
        )
    )
    paper_balance_usdt: float = field(default_factory=lambda: env_float("CRYPTO_PAPER_BALANCE_USDT", 1000.0))
    risk_profile: str = field(default_factory=lambda: os.getenv("CRYPTO_RISK_PROFILE", "conservative").strip().lower())
    max_position_pct: float = field(default_factory=lambda: env_float("CRYPTO_MAX_POSITION_PCT", 5.0))
    max_trade_risk_pct: float = field(default_factory=lambda: env_float("CRYPTO_MAX_TRADE_RISK_PCT", 1.0))
    max_daily_loss_pct: float = field(default_factory=lambda: env_float("CRYPTO_MAX_DAILY_LOSS_PCT", 2.0))
    max_open_positions: int = field(default_factory=lambda: env_int("CRYPTO_MAX_OPEN_POSITIONS", 3))
    max_trades_per_day: int = field(default_factory=lambda: env_int("CRYPTO_MAX_TRADES_PER_DAY", 6))
    cooldown_after_loss_minutes: int = field(default_factory=lambda: env_int("CRYPTO_COOLDOWN_AFTER_LOSS_MINUTES", 30))


def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
