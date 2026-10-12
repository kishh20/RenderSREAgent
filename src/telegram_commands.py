"""Interactive two-way Telegram Bot command handler (/status, /diagnose, /logs, /restart)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.ai_diagnoser import diagnose_failure_with_gemini
from src.alerter import send_telegram_alert
from src.checker import CheckResult
from src.config import MonitorConfig
from src.render_client import fetch_render_logs, restart_and_verify_service
from src.state_manager import AgentState

logger = logging.getLogger(__name__)


def process_telegram_commands(
    config: MonitorConfig,
    state: AgentState,
    latest_check: CheckResult,
    metrics: dict[str, Any],
) -> None:
    """
    Poll Telegram `getUpdates` for any commands sent by the authorized owner (`telegram_chat_id`)
    since `state.last_telegram_update_id` and reply immediately.
    Supported commands: /status, /diagnose, /logs, /restart
    """
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return

    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/getUpdates"
    params: dict[str, int] = {"timeout": 2, "limit": 20}
    if state.last_telegram_update_id > 0:
        params["offset"] = state.last_telegram_update_id + 1

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return
        updates = resp.json().get("result", [])
    except Exception as exc:
        logger.warning("Failed to poll Telegram updates: %s", exc)
        return

    if not updates:
        return

    # Deduplicate commands received between runs so we only execute each unique command once
    commands_to_run: list[str] = []
    for upd in updates:
        upd_id = int(upd.get("update_id", 0))
        if upd_id > state.last_telegram_update_id:
            state.last_telegram_update_id = upd_id

        msg = upd.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != str(config.telegram_chat_id):
            continue

        text = str(msg.get("text", "")).strip().lower().split("@")[0]
        if text in ("/status", "/diagnose", "/logs", "/restart") and text not in commands_to_run:
            commands_to_run.append(text)

    for cmd in commands_to_run:
        logger.info("Executing Telegram command from owner: %s", cmd)
        if cmd == "/status":
            icon = "🟢" if latest_check.is_up else "🔴"
            reply = (
                f"{icon} LIVE STATUS: {config.app_name}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• State: {'UP' if latest_check.is_up else 'DOWN'} (HTTP {latest_check.status_code})\n"
                f"• Latency: {latest_check.latency_ms} ms\n"
                f"• 24h / 7d Uptime: {metrics['uptime_24h']}% / {metrics['uptime_7d']}%\n"
                f"• Avg / P95 Latency: {metrics['avg_latency_24h']} ms / {metrics['p95_latency_24h']} ms\n"
                f"• Cold Starts (24h): {metrics['cold_starts_24h']}\n"
                f"• SSL Expiry: {metrics['ssl_days']} days\n"
                f"• Checked At: {latest_check.timestamp_ist}\n"
                f"• Dashboard: https://kishh20.github.io/RenderSREAgent/"
            )
            send_telegram_alert(config, reply)

        elif cmd == "/logs":
            lines = fetch_render_logs(config, limit=20)
            snippet = "\n".join(lines[-15:]) if lines else "(No recent log lines returned by Render API)"
            reply = f"📜 LATEST RENDER LOGS ({config.app_name}):\n━━━━━━━━━━━━━━━━━━━━━━\n{snippet[:3500]}"
            send_telegram_alert(config, reply)

        elif cmd == "/diagnose":
            lines = fetch_render_logs(config, limit=100)
            diag = diagnose_failure_with_gemini(config, latest_check, lines)
            reply = (
                f"🤖 GEMINI 3.8 FLASH LOG DIAGNOSIS\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{diag}"
            )
            send_telegram_alert(config, reply)

        elif cmd == "/restart":
            if len(state.restart_timestamps) >= config.max_restarts_per_hour:
                send_telegram_alert(
                    config,
                    f"⚠️ Restart skipped: hourly cap ({config.max_restarts_per_hour}/hr) reached.",
                )
            else:
                outcome = restart_and_verify_service(config)
                if outcome.triggered:
                    state.restart_timestamps.append(latest_check.epoch_sec)
                send_telegram_alert(
                    config,
                    f"🛠️ MANUAL RESTART RESULT:\n━━━━━━━━━━━━━━━━━━━━━━\n{outcome.message}",
                )
