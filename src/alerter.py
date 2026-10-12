"""Multi-channel alerting (Telegram primary + SMTP Email fallback) and message formatters."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import requests

from src.checker import CheckResult
from src.config import MonitorConfig
from src.render_client import SelfHealOutcome

logger = logging.getLogger(__name__)


def send_telegram_alert(config: MonitorConfig, message: str) -> bool:
    """Send an alert message via Telegram Bot API. Returns True on HTTP 200."""
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.info("Telegram credentials not configured; skipping Telegram alert.")
        return False

    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            logger.info("Telegram alert delivered successfully.")
            return True
        logger.warning("Telegram API returned HTTP %d: %s", resp.status_code, resp.text[:150])
        return False
    except requests.RequestException as exc:
        logger.warning("Telegram alert delivery failed: %s", exc)
        return False


def send_email_alert(config: MonitorConfig, subject: str, body: str) -> bool:
    """Send a fallback alert via SMTP email using Python stdlib `smtplib`."""
    if not (config.smtp_user and config.smtp_password and config.alert_email_to):
        logger.info("SMTP email credentials not configured; skipping email fallback.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.smtp_user
    msg["To"] = config.alert_email_to
    msg.set_content(body)

    try:
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=15) as server:
                server.login(config.smtp_user, config.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(config.smtp_user, config.smtp_password)
                server.send_message(msg)
        logger.info("Fallback email alert delivered to %s.", config.alert_email_to)
        return True
    except Exception as exc:
        logger.error("Failed to send fallback email alert: %s", exc)
        return False


def dispatch_alert(config: MonitorConfig, subject: str, message: str) -> bool:
    """
    Send alert to Telegram (primary). If Telegram fails or is unconfigured,
    automatically fall back to SMTP email.
    """
    sent_tg = send_telegram_alert(config, message)
    if sent_tg:
        return True
    logger.info("Falling back to SMTP email alert for: %s", subject)
    return send_email_alert(config, subject, message)


def format_down_alert(
    config: MonitorConfig,
    check: CheckResult,
    ai_diagnosis: str,
) -> tuple[str, str]:
    """Build subject and message for UP -> DOWN transition."""
    subject = f"🚨 [DOWN] {config.app_name} is unreachable"
    body = (
        f"🚨 SERVICE DOWN: {config.app_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• URL: {config.target_url}\n"
        f"• Reason: {check.error_reason}\n"
        f"• HTTP Status: {check.status_code}\n"
        f"• Latency: {check.latency_ms} ms (after {check.attempts_used} retries)\n"
        f"• Timestamp: {check.timestamp_ist}\n\n"
        f"🤖 Gemini SRE Diagnosis:\n{ai_diagnosis}"
    )
    return subject, body


def format_recovery_alert(
    config: MonitorConfig,
    check: CheckResult,
    outage_duration_sec: int,
) -> tuple[str, str]:
    """Build subject and message for DOWN -> UP recovery transition."""
    mins, secs = divmod(outage_duration_sec, 60)
    dur_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    subject = f"✅ [RECOVERED] {config.app_name} is back UP"
    body = (
        f"✅ SERVICE RECOVERED: {config.app_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• URL: {config.target_url}\n"
        f"• HTTP Status: {check.status_code}\n"
        f"• Response Time: {check.latency_ms} ms\n"
        f"• Total Downtime: {dur_str}\n"
        f"• Timestamp: {check.timestamp_ist}"
    )
    return subject, body


def format_slow_alert(
    config: MonitorConfig,
    check: CheckResult,
    consecutive_slow: int,
) -> tuple[str, str]:
    """Build subject and message for 3 consecutive slow checks (>5s)."""
    subject = f"⚠️ [DEGRADED] {config.app_name} High Latency ({check.latency_ms}ms)"
    body = (
        f"⚠️ HIGH LATENCY ALERT: {config.app_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Consecutive Slow Checks: {consecutive_slow} (> {config.slow_threshold_ms}ms)\n"
        f"• Latest Latency: {check.latency_ms} ms\n"
        f"• Cold Start Detected: {'Yes (>10s)' if check.is_cold_start else 'No'}\n"
        f"• HTTP Status: {check.status_code}\n"
        f"• Timestamp: {check.timestamp_ist}"
    )
    return subject, body


def format_self_heal_alert(
    config: MonitorConfig,
    outcome: SelfHealOutcome,
    restarts_this_hour: int,
    ai_diagnosis: str,
) -> tuple[str, str]:
    """Build subject and message for Render API self-healing outcome."""
    icon = "🛠️✅" if outcome.recovered else "🛠️❌"
    status_word = "RECOVERED" if outcome.recovered else "STILL DOWN"
    subject = f"{icon} [SELF-HEAL {status_word}] {config.app_name}"
    body = (
        f"{icon} AUTO-HEAL REPORT: {config.app_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Action: Render Container Restart (0 build mins)\n"
        f"• Outcome: {outcome.message}\n"
        f"• Restarts Used (Last 1h): {restarts_this_hour}/{config.max_restarts_per_hour}\n\n"
        f"🤖 Gemini SRE Diagnosis:\n{ai_diagnosis}"
    )
    return subject, body


def format_ssl_alert(config: MonitorConfig, check: CheckResult) -> tuple[str, str]:
    """Build subject and message for SSL certificate expiry warning (<14 days)."""
    subject = f"🔒 [SSL WARNING] {config.app_name} cert expires in {check.ssl_days_remaining} days"
    body = (
        f"🔒 SSL CERTIFICATE EXPIRY WARNING\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Service: {config.app_name}\n"
        f"• URL: {config.target_url}\n"
        f"• Days Remaining: {check.ssl_days_remaining} days (Threshold: {config.ssl_expiry_warn_days}d)\n"
        f"• Timestamp: {check.timestamp_ist}"
    )
    return subject, body


def format_daily_summary(
    config: MonitorConfig,
    metrics: dict[str, Any],
    timestamp_ist: str,
) -> tuple[str, str]:
    """Build daily 09:00 IST summary report."""
    subject = f"📊 [DAILY SRE DIGEST] {config.app_name} — {metrics['uptime_24h']}% Uptime"
    body = (
        f"📊 DAILY SRE SUMMARY (9:00 IST)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Service: {config.app_name}\n"
        f"• 24h Uptime: {metrics['uptime_24h']}% ({metrics['checks_24h']} checks)\n"
        f"• 7d Uptime: {metrics['uptime_7d']}%\n"
        f"• Avg / P95 Latency (24h): {metrics['avg_latency_24h']} ms / {metrics['p95_latency_24h']} ms\n"
        f"• Cold Starts (24h): {metrics['cold_starts_24h']}\n"
        f"• Incidents (24h): {metrics['incidents_24h']}\n"
        f"• Auto-Restarts (24h): {metrics['restarts_24h']}\n"
        f"• SSL Cert Expiry: {metrics['ssl_days']} days\n"
        f"• Report Time: {timestamp_ist}"
    )
    return subject, body
