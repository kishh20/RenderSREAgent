"""Metrics calculator and static HTML status dashboard generator (GitHub Pages ready)."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from src.config import MonitorConfig
from src.state_manager import AgentState


def _percentile(sorted_vals: list[int], pct: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, int(round((pct / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def compute_metrics(
    rows: list[dict[str, str]],
    state: AgentState,
    now_epoch: float,
) -> dict[str, Any]:
    """Calculate 24h/7d/30d uptime, avg/p95 latency, cold-start counts, and incident counts."""
    cut_24h = now_epoch - 86400
    cut_7d = now_epoch - (7 * 86400)
    cut_30d = now_epoch - (30 * 86400)

    rows_24h, rows_7d, rows_30d = [], [], []
    for r in rows:
        try:
            ep = float(r.get("epoch_sec", 0))
        except ValueError:
            continue
        if ep >= cut_30d:
            rows_30d.append(r)
        if ep >= cut_7d:
            rows_7d.append(r)
        if ep >= cut_24h:
            rows_24h.append(r)

    def _uptime_pct(subset: list[dict[str, str]]) -> str:
        if not subset:
            return "100.00"
        up_count = sum(1 for r in subset if r.get("status") == "UP")
        return f"{(up_count / len(subset)) * 100:.2f}"

    latencies_24h = sorted(
        int(r.get("latency_ms", 0))
        for r in rows_24h
        if r.get("status") == "UP" and str(r.get("latency_ms", "")).isdigit()
    )
    avg_lat_24h = int(sum(latencies_24h) / len(latencies_24h)) if latencies_24h else 0
    p95_lat_24h = _percentile(latencies_24h, 95.0)
    cold_starts_24h = sum(1 for r in rows_24h if r.get("is_cold_start") == "1")

    incidents_24h = sum(
        1 for inc in state.incidents if float(inc.get("start_epoch", 0)) >= cut_24h
    )
    restarts_24h = sum(1 for ts in state.restart_timestamps if ts >= cut_24h)

    latest_ssl = "N/A"
    if rows and rows[-1].get("ssl_days_remaining"):
        latest_ssl = rows[-1]["ssl_days_remaining"]

    return {
        "uptime_24h": _uptime_pct(rows_24h),
        "uptime_7d": _uptime_pct(rows_7d),
        "uptime_30d": _uptime_pct(rows_30d),
        "checks_24h": len(rows_24h),
        "avg_latency_24h": avg_lat_24h,
        "p95_latency_24h": p95_lat_24h,
        "cold_starts_24h": cold_starts_24h,
        "incidents_24h": incidents_24h,
        "restarts_24h": restarts_24h,
        "ssl_days": latest_ssl,
    }


def _build_svg_latency_chart(rows: list[dict[str, str]]) -> str:
    """Render a zero-dependency responsive SVG sparkline + area chart for recent checks."""
    recent = rows[-72:]  # ~12 hours at 10-min intervals
    if not recent:
        return '<div class="empty-state">No telemetry points recorded yet.</div>'

    width = 860
    height = 210
    pad_x = 44
    pad_y = 24
    plot_w = width - pad_x * 2
    plot_h = height - pad_y * 2

    latencies = [max(1, int(r.get("latency_ms", 0) or 0)) for r in recent]
    max_lat = max(max(latencies), 1000)

    pts: list[tuple[float, float, dict[str, str]]] = []
    for idx, r in enumerate(recent):
        x = pad_x + (idx / max(1, len(recent) - 1)) * plot_w
        lat = int(r.get("latency_ms", 0) or 0)
        y = pad_y + plot_h - min(1.0, lat / max_lat) * plot_h
        pts.append((x, y, r))

    poly_points = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
    area_points = (
        f"{pts[0][0]:.1f},{pad_y + plot_h:.1f} "
        + poly_points
        + f" {pts[-1][0]:.1f},{pad_y + plot_h:.1f}"
    )

    dots_svg = []
    for x, y, r in pts:
        is_down = r.get("status") != "UP"
        is_cold = r.get("is_cold_start") == "1"
        color = "#ef4444" if is_down else ("#f59e0b" if is_cold else "#10b981")
        radius = "4.5" if (is_down or is_cold) else "2.5"
        tip = html.escape(
            f"{r.get('timestamp_ist')} | {r.get('status')} | {r.get('latency_ms')}ms"
        )
        dots_svg.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{color}"><title>{tip}</title></circle>'
        )

    return f"""
    <svg viewBox="0 0 {width} {height}" class="latency-svg" role="img" aria-label="Latency chart">
      <defs>
        <linearGradient id="latGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#10b981" stop-opacity="0.32"/>
          <stop offset="100%" stop-color="#10b981" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      <line x1="{pad_x}" y1="{pad_y}" x2="{width - pad_x}" y2="{pad_y}" stroke="#1f2937" stroke-dasharray="4"/>
      <line x1="{pad_x}" y1="{pad_y + plot_h / 2:.1f}" x2="{width - pad_x}" y2="{pad_y + plot_h / 2:.1f}" stroke="#1f2937" stroke-dasharray="4"/>
      <line x1="{pad_x}" y1="{pad_y + plot_h}" x2="{width - pad_x}" y2="{pad_y + plot_h}" stroke="#374151"/>
      <text x="6" y="{pad_y + 4}" fill="#9ca3af" font-size="11">{max_lat}ms</text>
      <text x="6" y="{pad_y + plot_h / 2 + 4:.1f}" fill="#9ca3af" font-size="11">{max_lat // 2}ms</text>
      <text x="6" y="{pad_y + plot_h}" fill="#9ca3af" font-size="11">0ms</text>
      <polygon points="{area_points}" fill="url(#latGrad)"/>
      <polyline fill="none" stroke="#10b981" stroke-width="2.2" points="{poly_points}"/>
      {''.join(dots_svg)}
    </svg>
    """


def generate_dashboard_html(
    config: MonitorConfig,
    rows: list[dict[str, str]],
    state: AgentState,
    metrics: dict[str, Any],
    output_path: Path | None = None,
) -> str:
    """Generate the static HTML status page and write it to `public/index.html`."""
    target_path = output_path or config.dashboard_html_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    is_up = state.current_status == "UP"
    status_badge_class = "badge-up" if is_up else "badge-down"
    status_headline = "All Systems Operational" if is_up else "Service Outage Detected"

    # Build 90-bar uptime strip
    recent_90 = rows[-90:]
    bars_html = []
    for r in recent_90:
        st = r.get("status", "UP")
        cold = r.get("is_cold_start") == "1"
        cls = "bar-up" if st == "UP" and not cold else ("bar-cold" if st == "UP" else "bar-down")
        tip = html.escape(
            f"{r.get('timestamp_ist', '')} — {st} ({r.get('latency_ms', '0')} ms)"
        )
        bars_html.append(f'<div class="uptime-bar {cls}" title="{tip}"></div>')

    # Build incidents table/cards
    incident_items = []
    for inc in state.incidents[:15]:
        inc_id = html.escape(str(inc.get("id", "")))
        start_t = html.escape(str(inc.get("start_time_ist", "")))
        end_t = html.escape(str(inc.get("end_time_ist", "Ongoing")))
        reason = html.escape(str(inc.get("reason", "")))
        ai_diag = html.escape(str(inc.get("ai_diagnosis", ""))).replace("\n", "<br>")
        healed = inc.get("auto_restart_succeeded", False)
        heal_badge = (
            '<span class="pill pill-healed">Auto-Healed via Render API</span>'
            if healed
            else (
                '<span class="pill pill-warn">Auto-Restart Attempted</span>'
                if inc.get("auto_restart_attempted")
                else ""
            )
        )
        ai_block = (
            f'<div class="ai-box"><strong>🤖 Gemini 3.8 Flash Triage:</strong><br>{ai_diag}</div>'
            if ai_diag
            else ""
        )
        incident_items.append(
            f"""
            <div class="incident-card">
              <div class="incident-header">
                <span class="inc-id">{inc_id}</span>
                {heal_badge}
                <span class="inc-time">{start_t} → {end_t}</span>
              </div>
              <div class="inc-reason"><strong>Failing Check:</strong> {reason}</div>
              {ai_block}
            </div>
            """
        )

    incidents_html = (
        "\n".join(incident_items)
        if incident_items
        else '<div class="empty-state">🎉 Zero incidents recorded in the last 30 days.</div>'
    )

    chart_svg = _build_svg_latency_chart(rows)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="300">
  <title>{html.escape(config.app_name)} — SRE Status & Self-Healing Monitor</title>
  <style>
    :root {{
      --bg: #0b0f19;
      --card: #111827;
      --border: #1f2937;
      --text: #f9fafb;
      --muted: #9ca3af;
      --green: #10b981;
      --amber: #f59e0b;
      --red: #ef4444;
      --blue: #3b82f6;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 2rem 1rem;
    }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
      padding: 1.5rem;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      margin-bottom: 1.5rem;
    }}
    .title-group h1 {{ font-size: 1.35rem; font-weight: 700; }}
    .title-group a {{ color: var(--muted); font-size: 0.88rem; text-decoration: none; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.5rem 1rem;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.9rem;
    }}
    .badge-up {{ background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.35); }}
    .badge-down {{ background: rgba(239, 68, 68, 0.15); color: var(--red); border: 1px solid rgba(239, 68, 68, 0.35); }}
    .dot {{ width: 9px; height: 9px; border-radius: 50%; background: currentColor; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .metric-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.15rem;
    }}
    .metric-label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }}
    .metric-val {{ font-size: 1.55rem; font-weight: 700; margin-top: 0.25rem; }}
    .metric-sub {{ color: var(--muted); font-size: 0.78rem; margin-top: 0.15rem; }}
    .panel {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.4rem;
      margin-bottom: 1.5rem;
    }}
    .panel-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }}
    .panel-header h2 {{ font-size: 1.05rem; font-weight: 600; }}
    .uptime-strip {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(6px, 1fr));
      gap: 3px;
      height: 30px;
      align-items: stretch;
    }}
    .uptime-bar {{ border-radius: 3px; transition: opacity 0.15s; }}
    .uptime-bar:hover {{ opacity: 0.75; }}
    .bar-up {{ background: var(--green); }}
    .bar-cold {{ background: var(--amber); }}
    .bar-down {{ background: var(--red); }}
    .latency-svg {{ width: 100%; height: auto; display: block; }}
    .incident-card {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem;
      margin-bottom: 0.85rem;
      background: rgba(255, 255, 255, 0.015);
    }}
    .incident-header {{
      display: flex;
      align-items: center;
      gap: 0.65rem;
      flex-wrap: wrap;
      margin-bottom: 0.45rem;
    }}
    .inc-id {{ font-family: monospace; font-weight: 700; color: var(--red); }}
    .inc-time {{ color: var(--muted); font-size: 0.84rem; margin-left: auto; }}
    .inc-reason {{ font-size: 0.92rem; margin-bottom: 0.5rem; }}
    .ai-box {{
      background: rgba(59, 130, 246, 0.09);
      border-left: 3px solid var(--blue);
      padding: 0.65rem 0.85rem;
      border-radius: 6px;
      font-size: 0.86rem;
      color: #dbeafe;
    }}
    .pill {{
      font-size: 0.75rem;
      padding: 0.15rem 0.55rem;
      border-radius: 999px;
      font-weight: 600;
    }}
    .pill-healed {{ background: rgba(16, 185, 129, 0.18); color: var(--green); }}
    .pill-warn {{ background: rgba(245, 158, 11, 0.18); color: var(--amber); }}
    .empty-state {{ color: var(--muted); font-size: 0.92rem; padding: 1rem 0; text-align: center; }}
    .footer {{ text-align: center; color: var(--muted); font-size: 0.8rem; margin-top: 2rem; }}
  </style>
</head>
<body>
  <div class="container">
    <header class="header">
      <div class="title-group">
        <h1>{html.escape(config.app_name)}</h1>
        <a href="{html.escape(config.target_url)}" target="_blank" rel="noopener">{html.escape(config.target_url)}</a>
      </div>
      <div class="badge {status_badge_class}">
        <span class="dot"></span>
        {status_headline}
      </div>
    </header>

    <section class="grid">
      <div class="metric-card">
        <div class="metric-label">Uptime (24h)</div>
        <div class="metric-val">{metrics['uptime_24h']}%</div>
        <div class="metric-sub">7d: {metrics['uptime_7d']}% · 30d: {metrics['uptime_30d']}%</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Avg Latency (24h)</div>
        <div class="metric-val">{metrics['avg_latency_24h']} ms</div>
        <div class="metric-sub">P95: {metrics['p95_latency_24h']} ms</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Cold Starts (24h)</div>
        <div class="metric-val">{metrics['cold_starts_24h']}</div>
        <div class="metric-sub">Threshold: &gt;{config.cold_start_threshold_ms // 1000}s</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">SSL Certificate</div>
        <div class="metric-val">{metrics['ssl_days']} d</div>
        <div class="metric-sub">Warn &lt; {config.ssl_expiry_warn_days} days</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Self-Heals (24h)</div>
        <div class="metric-val">{metrics['restarts_24h']}</div>
        <div class="metric-sub">Cap: {config.max_restarts_per_hour}/hr via Render API</div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>Recent Check History (Last 90 Checks)</h2>
        <span style="color: var(--muted); font-size: 0.82rem;">Green = UP · Amber = Cold Start (&gt;10s) · Red = DOWN</span>
      </div>
      <div class="uptime-strip">
        {''.join(bars_html) if bars_html else '<div class="empty-state">No checks recorded yet</div>'}
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>Response Latency Trend (ms)</h2>
        <span style="color: var(--muted); font-size: 0.82rem;">Last check: {html.escape(state.last_check_ist or 'N/A')}</span>
      </div>
      {chart_svg}
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>Incidents, Self-Healing &amp; Gemini AI Root-Cause Triage</h2>
      </div>
      {incidents_html}
    </section>

    <footer class="footer">
      Powered by Render SRE Self-Healing Agent · Zero-Commit-Spam Orphan <code>gh-pages</code> Telemetry · Updated {html.escape(state.last_check_ist)}
    </footer>
  </div>
</body>
</html>
"""
    target_path.write_text(html_doc, encoding="utf-8")
    return html_doc
