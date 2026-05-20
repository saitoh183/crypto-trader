from __future__ import annotations

import argparse
import html
import json
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    conn.close()

    return {
        "latest": dict(latest) if latest else None,
        "snapshots": [dict(row) for row in snapshots],
        "news": [dict(row) for row in news],
    }


def render_dashboard() -> str:
    data = load_dashboard_data()
    latest = data["latest"]
    snapshots = data["snapshots"]
    news = data["news"]
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
