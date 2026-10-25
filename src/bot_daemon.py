"""Real-time Telegram Bot long-polling responder for instant (<1s) replies to /status, /diagnose, /logs, /restart."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import requests

from src.ai_diagnoser import diagnose_failure_with_gemini
from src.alerter import send_telegram_alert
from src.checker import perform_health_check
from src.config import BASE_DIR, MonitorConfig
from src.dashboard import compute_metrics
from src.render_client import fetch_render_logs, restart_and_verify_service
from src.state_manager import AgentState, append_and_prune_csv

logger = logging.getLogger("TelegramBotDaemon")


def _load_dotenv_if_present() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def _keep_alive_worker(config: MonitorConfig) -> None:
    """Background worker that pings target_url every 4 minutes to guarantee zero Render idle sleep."""
    logger.info("Keep-alive background worker active (pinging every 4 minutes)...")
    while True:
        try:
            r = requests.get(config.target_url, timeout=25)
            logger.info("Keep-alive ping sent to %s -> HTTP %d (%dms)", config.target_url, r.status_code, int(r.elapsed.total_seconds() * 1000))
        except Exception as exc:
            logger.warning("Keep-alive ping exception: %s", exc)
        time.sleep(240)  # 4 minutes interval (Render sleeps after 15m)


def handle_single_command(cmd: str, config: MonitorConfig) -> None:
    """Execute a single Telegram slash command and send an immediate response."""
    state = AgentState.load(config.state_json_path)

    if cmd in ("/start", "/help"):
        reply = (
            f"🛡️ {config.app_name} — Interactive SRE Bot\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Available commands:\n"
            "• /status — Live health check, latency, uptime % & SSL\n"
            "• /diagnose — Gemini 3.8 Flash AI audit of Render logs\n"
            "• /logs — View latest 15 Render service log lines\n"
            "• /restart — Restart Render container & verify recovery"
        )
        send_telegram_alert(config, reply)
        return

    if cmd == "/status":
        check = perform_health_check(config)
        rows = append_and_prune_csv(config.checks_csv_path, check)
        metrics = compute_metrics(rows, state, check.epoch_sec)
        icon = "🟢" if check.is_up else "🔴"
        reply = (
            f"{icon} LIVE STATUS: {config.app_name}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• State: {'UP' if check.is_up else 'DOWN'} (HTTP {check.status_code})\n"
            f"• Latency: {check.latency_ms} ms\n"
            f"• 24h / 7d Uptime: {metrics['uptime_24h']}% / {metrics['uptime_7d']}%\n"
            f"• Avg / P95 Latency: {metrics['avg_latency_24h']} ms / {metrics['p95_latency_24h']} ms\n"
            f"• Cold Starts (24h): {metrics['cold_starts_24h']}\n"
            f"• SSL Cert Expiry: {check.ssl_days_remaining} days\n"
            f"• Checked At: {check.timestamp_ist}\n"
            f"• Dashboard: https://kishh20.github.io/RenderSREAgent/"
        )
        send_telegram_alert(config, reply)

    elif cmd == "/logs":
        lines = fetch_render_logs(config, limit=25)
        non_empty = [ln for ln in lines if not ln.endswith("==>")]
        snippet = "\n".join(non_empty[-15:]) if non_empty else "(No recent log lines returned by Render API)"
        reply = (
            f"📜 LATEST RENDER LOGS ({config.app_name})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{snippet[:3500]}"
        )
        send_telegram_alert(config, reply)

    elif cmd == "/diagnose":
        send_telegram_alert(config, "🔍 Fetching Render logs & running Gemini 3.8 Flash analysis...")
        check = perform_health_check(config)
        lines = fetch_render_logs(config, limit=100)
        diag = diagnose_failure_with_gemini(config, check, lines)
        reply = (
            f"🤖 GEMINI 3.8 FLASH SRE AUDIT ({config.app_name})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{diag}"
        )
        send_telegram_alert(config, reply)

    elif cmd == "/restart":
        send_telegram_alert(
            config,
            "🛠️ Triggering Render container restart (0 build mins) & waiting 20s to re-verify...",
        )
        outcome = restart_and_verify_service(config)
        if outcome.triggered:
            state.restart_timestamps.append(time.time())
            state.save(config.state_json_path)
        send_telegram_alert(
            config,
            f"🛠️ MANUAL RESTART RESULT:\n━━━━━━━━━━━━━━━━━━━━━━\n{outcome.message}",
        )


def run_polling_loop() -> None:
    """Long-poll Telegram Bot API for instant interactive command execution."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _load_dotenv_if_present()
    config = MonitorConfig.from_env()

    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
        return

    state = AgentState.load(config.state_json_path)
    offset = state.last_telegram_update_id + 1 if state.last_telegram_update_id else 0
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/getUpdates"

    logger.info("Real-time Telegram Bot daemon listening for commands...")
    ping_thread = threading.Thread(target=_keep_alive_worker, args=(config,), daemon=True)
    ping_thread.start()
    while True:
        try:
            params = {"timeout": 25, "limit": 10}
            if offset > 0:
                params["offset"] = offset

            resp = requests.get(url, params=params, timeout=32)
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                for upd in updates:
                    upd_id = int(upd.get("update_id", 0))
                    offset = max(offset, upd_id + 1)
                    state.last_telegram_update_id = upd_id
                    state.save(config.state_json_path)

                    msg = upd.get("message") or {}
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if chat_id != str(config.telegram_chat_id):
                        continue

                    text = str(msg.get("text", "")).strip().lower().split("@")[0]
                    if text.startswith("/"):
                        logger.info("Received instant command: %s", text)
                        handle_single_command(text, config)
            else:
                time.sleep(3)
        except Exception as exc:
            logger.warning("Polling error (%s), retrying in 3s...", exc)
            time.sleep(3)


if __name__ == "__main__":
    run_polling_loop()
