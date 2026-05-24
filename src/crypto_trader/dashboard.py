from __future__ import annotations

import argparse
import html
import json
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from .config import get_settings
from .db import connect, init_db


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def fmt_num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}"


def load_dashboard_data() -> dict[str, object]:
    settings = get_settings()
    conn = connect(settings.database_path)
    init_db(conn)

    latest = conn.execute("SELECT * FROM run_summaries ORDER BY collected_at_utc DESC LIMIT 1").fetchone()
    snapshots = conn.execute(
        """
        SELECT * FROM market_snapshots
        WHERE collected_at_utc = COALESCE((SELECT MAX(collected_at_utc) FROM market_snapshots), '')
        ORDER BY symbol
        """
    ).fetchall()
    news = conn.execute(
        """
        SELECT source, title, link, published_utc, matched_symbols
        FROM news_items
        ORDER BY COALESCE(published_utc, first_seen_utc) DESC
        LIMIT 40
        """
    ).fetchall()
    signals = conn.execute(
        """
        SELECT generated_at_utc, symbol, interval, action, confidence, price, reason
        FROM trade_signals
        ORDER BY generated_at_utc DESC
        LIMIT 25
        """
    ).fetchall()
    orders = conn.execute(
        """
        SELECT generated_at_utc, symbol, side, quantity, price, notional_usdt,
               fee_usdt, status, reason, realized_pnl_usdt
        FROM paper_orders
        ORDER BY generated_at_utc DESC
        LIMIT 25
        """
    ).fetchall()
    positions = conn.execute(
        "SELECT symbol, quantity, entry_price, cost_basis_usdt FROM paper_positions ORDER BY symbol"
    ).fetchall()
    account_row = conn.execute(
        "SELECT cash_usdt, updated_at_utc FROM paper_account WHERE account_id = 'default'"
    ).fetchone()
    signal_stats = conn.execute(
        """
        SELECT action, COUNT(*) AS count
        FROM trade_signals
        GROUP BY action
        """
    ).fetchall()
    order_stats = conn.execute(
        """
        SELECT status, side, COUNT(*) AS count,
               COALESCE(SUM(notional_usdt), 0) AS notional_usdt,
               COALESCE(SUM(realized_pnl_usdt), 0) AS realized_pnl_usdt
        FROM paper_orders
        GROUP BY status, side
        """
    ).fetchall()
    kill_switch_row = conn.execute("SELECT value FROM paper_settings WHERE key = 'kill_switch'").fetchone()
    conn.close()

    return {
        "latest": dict(latest) if latest else None,
        "snapshots": [dict(row) for row in snapshots],
        "news": [dict(row) for row in news],
        "signals": [dict(row) for row in signals],
        "orders": [dict(row) for row in orders],
        "positions": [dict(row) for row in positions],
        "account": dict(account_row) if account_row else None,
        "signal_stats": [dict(row) for row in signal_stats],
        "order_stats": [dict(row) for row in order_stats],
        "kill_switch": bool(kill_switch_row and kill_switch_row["value"].strip().lower() in {"1", "true", "yes", "on"}),
    }


def fmt_pnl(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.2f}"


def render_dashboard() -> str:
    data = load_dashboard_data()
    latest = data["latest"]
    snapshots = data["snapshots"]
    news = data["news"]
    signals = data["signals"]
    orders = data["orders"]
    positions = data["positions"]
    account = data["account"]
    signal_stats_rows = cast(list[dict[str, Any]], data["signal_stats"])
    order_stats = cast(list[dict[str, Any]], data["order_stats"])
    signal_stats = {row["action"]: row["count"] for row in signal_stats_rows}
    kill_switch = bool(data["kill_switch"])
    generated = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    latest_html = "<p>No collection run yet.</p>"
    if latest:
        latest_html = f"""
        <div class="card bias">
          <div class="label">Market bias</div>
          <div class="big">{html.escape(latest['market_bias'])}</div>
          <pre>{html.escape(latest['summary'])}</pre>
        </div>
        """

    snapshot_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['symbol'])}</td>
          <td>{html.escape(row['trend'])}</td>
          <td>{fmt_num(row['last_close'])}</td>
          <td>{fmt_pct(row['change_1h_pct'])}</td>
          <td>{fmt_pct(row['change_24h_pct'])}</td>
          <td>{fmt_num(row['sma_20'])}</td>
          <td>{fmt_num(row['rsi_14'])}</td>
          <td>{html.escape(row['notes'])}</td>
        </tr>
        """
        for row in snapshots
    ) or "<tr><td colspan='8'>No market snapshots yet.</td></tr>"

    news_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['source'])}</td>
          <td><a href="{html.escape(row['link'])}" target="_blank" rel="noopener noreferrer">{html.escape(row['title'])}</a></td>
          <td>{html.escape(row['matched_symbols'] or '')}</td>
          <td>{html.escape(row['published_utc'] or '')}</td>
        </tr>
        """
        for row in news
    ) or "<tr><td colspan='4'>No news yet.</td></tr>"

    # Paper account summary card
    if account:
        cash = float(account["cash_usdt"])
        cost_basis_total = sum(float(p["cost_basis_usdt"]) for p in positions)
        account_html = f"""
        <div class="card">
          <div class="label">Paper account · paper trading only</div>
          <div style="display:flex;gap:32px;margin-top:10px;flex-wrap:wrap;">
            <div><div class="label">Cash</div><div class="big">{fmt_num(cash)} USDT</div></div>
            <div><div class="label">Positions (cost basis)</div><div class="big">{fmt_num(cost_basis_total)} USDT</div></div>
            <div><div class="label">Last updated</div><div class="big" style="font-size:16px">{html.escape(account['updated_at_utc'])}</div></div>
          </div>
        </div>
        """
    else:
        account_html = ""

    filled_orders = sum(int(row["count"]) for row in order_stats if row["status"] == "FILLED")
    rejected_orders = sum(int(row["count"]) for row in order_stats if row["status"] == "REJECTED")
    skipped_orders = sum(int(row["count"]) for row in order_stats if row["status"] == "SKIPPED")
    realized_pnl = sum(float(row["realized_pnl_usdt"]) for row in order_stats)
    safety_html = f"""
    <div class="card">
      <div class="label">Trading readiness / safety</div>
      <div class="status-grid">
        <div><div class="label">Mode</div><div class="big">Paper only</div></div>
        <div><div class="label">Kill switch</div><div class="big {'danger' if kill_switch else 'ok'}">{'ON' if kill_switch else 'OFF'}</div></div>
        <div><div class="label">Signals</div><div class="big">B {int(signal_stats.get('BUY', 0))} / S {int(signal_stats.get('SELL', 0))} / H {int(signal_stats.get('HOLD', 0))}</div></div>
        <div><div class="label">Orders</div><div class="big">{filled_orders} filled · {rejected_orders} rejected · {skipped_orders} skipped</div></div>
        <div><div class="label">Realized paper P&amp;L</div><div class="big {'ok' if realized_pnl >= 0 else 'danger'}">{fmt_pnl(realized_pnl)} USDT</div></div>
      </div>
      <p class="muted">Live execution remains disabled until backtesting, risk gates, kill switch, approval records, exchange key checks, and your explicit live approval are all in place.</p>
    </div>
    """

    # Open paper positions table
    position_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(p['symbol'])}</td>
          <td>{fmt_num(p['quantity'])}</td>
          <td>{fmt_num(p['entry_price'])}</td>
          <td>{fmt_num(p['cost_basis_usdt'])}</td>
        </tr>
        """
        for p in positions
    ) or "<tr><td colspan='4'>No open positions.</td></tr>"

    # Latest trade signals table
    signal_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['generated_at_utc'])}</td>
          <td>{html.escape(row['symbol'])}</td>
          <td><strong>{html.escape(row['action'])}</strong></td>
          <td>{fmt_num(row['confidence'])}</td>
          <td>{fmt_num(row['price'])}</td>
          <td class="muted">{html.escape(row['reason'])}</td>
        </tr>
        """
        for row in signals
    ) or "<tr><td colspan='6'>No signals yet.</td></tr>"

    # Recent paper orders table
    order_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['generated_at_utc'])}</td>
          <td>{html.escape(row['symbol'])}</td>
          <td>{html.escape(row['side'])}</td>
          <td>{html.escape(row['status'])}</td>
          <td>{fmt_num(row['notional_usdt'])}</td>
          <td>{fmt_pnl(row['realized_pnl_usdt'])}</td>
          <td class="muted">{html.escape(row['reason'])}</td>
        </tr>
        """
        for row in orders
    ) or "<tr><td colspan='7'>No paper orders yet.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Market Monitor</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }}
    body {{ margin: 0; background: #0b1020; color: #e8edf7; }}
    header {{ padding: 24px 32px; background: #111936; border-bottom: 1px solid #26345f; }}
    main {{ padding: 24px 32px; display: grid; gap: 24px; }}
    .muted {{ color: #9fb0d0; }}
    .card {{ background: #121a33; border: 1px solid #26345f; border-radius: 14px; padding: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }}
    .label {{ color: #9fb0d0; text-transform: uppercase; letter-spacing: .08em; font-size: 12px; }}
    .big {{ font-size: 28px; font-weight: 700; margin: 8px 0 14px; }}
    .status-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 18px; margin-top: 10px; }}
    .ok {{ color: #7ee787; }}
    .danger {{ color: #ff7b72; }}
    pre {{ white-space: pre-wrap; color: #cbd7f4; margin: 0; }}
    table {{ width: 100%; border-collapse: collapse; background: #121a33; border-radius: 14px; overflow: hidden; }}
    th, td {{ padding: 11px 12px; border-bottom: 1px solid #26345f; text-align: left; vertical-align: top; }}
    th {{ background: #18234a; color: #dce6ff; }}
    a {{ color: #8db7ff; }}
    .section-title {{ margin: 0 0 10px; }}
  </style>
</head>
<body>
  <header>
    <h1>Crypto Market Monitor</h1>
    <div class="muted">Generated {html.escape(generated)} · centralized market/news view · paper/research mode</div>
  </header>
  <main>
    {latest_html}
    {account_html}
    {safety_html}
    <section>
      <h2 class="section-title">Open paper positions</h2>
      <table>
        <thead><tr><th>Symbol</th><th>Quantity</th><th>Entry price</th><th>Cost basis (USDT)</th></tr></thead>
        <tbody>{position_rows}</tbody>
      </table>
    </section>
    <section>
      <h2 class="section-title">Latest paper signals (last 25)</h2>
      <table>
        <thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Action</th><th>Confidence</th><th>Price</th><th>Reason</th></tr></thead>
        <tbody>{signal_rows}</tbody>
      </table>
    </section>
    <section>
      <h2 class="section-title">Recent paper orders (last 25)</h2>
      <table>
        <thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Side</th><th>Status</th><th>Notional (USDT)</th><th>P&amp;L (USDT)</th><th>Reason</th></tr></thead>
        <tbody>{order_rows}</tbody>
      </table>
    </section>
    <section>
      <h2 class="section-title">Market snapshots</h2>
      <table>
        <thead><tr><th>Symbol</th><th>Trend</th><th>Last</th><th>1h</th><th>24h</th><th>SMA20</th><th>RSI14</th><th>Notes</th></tr></thead>
        <tbody>{snapshot_rows}</tbody>
      </table>
    </section>
    <section>
      <h2 class="section-title">Latest reputable news feeds</h2>
      <table>
        <thead><tr><th>Source</th><th>Title</th><th>Matches</th><th>Published</th></tr></thead>
        <tbody>{news_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def write_report() -> Path:
    settings = get_settings()
    settings.report_path.parent.mkdir(parents=True, exist_ok=True)
    settings.report_path.write_text(render_dashboard(), encoding="utf-8")
    return settings.report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render or serve the crypto monitor dashboard")
    parser.add_argument("--write", action="store_true", help="Write static HTML report")
    parser.add_argument("--serve", action="store_true", help="Serve reports directory")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)

    report_path = write_report()
    if args.write and not args.serve:
        print(json.dumps({"report_path": str(report_path)}, indent=2))
        return 0

    if args.serve:
        reports_dir = report_path.parent

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *handler_args, **handler_kwargs):
                super().__init__(*handler_args, directory=str(reports_dir), **handler_kwargs)

        server = ThreadingHTTPServer((args.host, args.port), Handler)
        print(f"Serving {reports_dir} on http://{args.host}:{args.port}/latest.html")
        server.serve_forever()

    print(render_dashboard())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
