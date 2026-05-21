from crypto_trader.config import Settings


def test_default_watchlist_includes_requested_usdt_pairs(monkeypatch):
    monkeypatch.delenv("CRYPTO_MONITOR_SYMBOLS", raising=False)

    settings = Settings()

    assert settings.symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")


def test_default_interval_is_fifteen_minutes(monkeypatch):
    monkeypatch.delenv("CRYPTO_MONITOR_INTERVAL", raising=False)

    settings = Settings()

    assert settings.interval == "15m"


def test_paper_trading_defaults_are_conservative(monkeypatch):
    for name in (
        "CRYPTO_PAPER_BALANCE_USDT",
        "CRYPTO_RISK_PROFILE",
        "CRYPTO_MAX_POSITION_PCT",
        "CRYPTO_MAX_TRADE_RISK_PCT",
        "CRYPTO_MAX_DAILY_LOSS_PCT",
        "CRYPTO_MAX_OPEN_POSITIONS",
        "CRYPTO_MAX_TRADES_PER_DAY",
        "CRYPTO_COOLDOWN_AFTER_LOSS_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.paper_balance_usdt == 1000.0
    assert settings.risk_profile == "conservative"
    assert settings.max_position_pct == 5.0
    assert settings.max_trade_risk_pct == 1.0
    assert settings.max_daily_loss_pct == 2.0
    assert settings.max_open_positions == 3
    assert settings.max_trades_per_day == 6
    assert settings.cooldown_after_loss_minutes == 30
