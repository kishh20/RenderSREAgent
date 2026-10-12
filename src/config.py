"""Configuration and environment variable loader for Render SRE Agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
BASE_DIR = Path(__file__).resolve().parent.parent


def now_ist() -> datetime:
    """Return current timezone-aware datetime in IST (UTC+5:30)."""
    return datetime.now(tz=IST)


def format_ist(dt: datetime | None = None) -> str:
    """Format a datetime in readable IST string."""
    target = dt.astimezone(IST) if dt else now_ist()
    return target.strftime("%Y-%m-%d %H:%M:%S IST")


@dataclass(frozen=True)
class MonitorConfig:
    """Immutable runtime configuration loaded from environment variables."""

    # Target service settings
    app_name: str
    target_url: str
    expected_keyword: str
    request_timeout_sec: int

    # Retry & thresholds
    max_retries: int
    retry_Backoff_base_sec: float
    slow_threshold_ms: int
    cold_start_threshold_ms: int
    consecutive_slow_limit: int
    consecutive_failures_for_heal: int
    ssl_expiry_warn_days: int
    max_restarts_per_hour: int

    # Render API (Self-Healing & Logs)
    render_api_key: str
    render_service_id: str

    # Gemini AI (Root-Cause Diagnosis)
    gemini_api_key: str
    gemini_model: str

    # Telegram Alerting (Primary)
    telegram_bot_token: str
    telegram_chat_id: str

    # Email Alerting (Fallback)
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    alert_email_to: str

    # Storage paths
    data_dir: Path
    public_dir: Path
    checks_csv_path: Path
    state_json_path: Path
    dashboard_html_path: Path

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> MonitorConfig:
        """Load configuration from environment variables with safe defaults."""
        root = base_dir or Path(os.getenv("MONITOR_BASE_DIR", str(BASE_DIR)))
        data_dir = root / "data"
        public_dir = root / "public"

        raw_url = os.getenv("TARGET_URL", "https://your-app.onrender.com/health").strip()

        return cls(
            app_name=os.getenv("APP_NAME", "Render Web Service").strip(),
            target_url=raw_url,
            expected_keyword=os.getenv("EXPECTED_KEYWORD", "ok").strip(),
            request_timeout_sec=int(os.getenv("REQUEST_TIMEOUT_SEC", "35")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_Backoff_base_sec=float(os.getenv("RETRY_BACKOFF_BASE_SEC", "2.0")),
            slow_threshold_ms=int(os.getenv("SLOW_THRESHOLD_MS", "5000")),
            cold_start_threshold_ms=int(os.getenv("COLD_START_THRESHOLD_MS", "10000")),
            consecutive_slow_limit=int(os.getenv("CONSECUTIVE_SLOW_LIMIT", "3")),
            consecutive_failures_for_heal=int(
                os.getenv("CONSECUTIVE_FAILURES_FOR_HEAL", "3")
            ),
            ssl_expiry_warn_days=int(os.getenv("SSL_EXPIRY_WARN_DAYS", "14")),
            max_restarts_per_hour=int(os.getenv("MAX_RESTARTS_PER_HOUR", "2")),
            render_api_key=os.getenv("RENDER_API_KEY", "").strip(),
            render_service_id=os.getenv("RENDER_SERVICE_ID", "").strip(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
            alert_email_to=os.getenv("ALERT_EMAIL_TO", "").strip(),
            data_dir=data_dir,
            public_dir=public_dir,
            checks_csv_path=data_dir / "checks.csv",
            state_json_path=data_dir / "state.json",
            dashboard_html_path=public_dir / "index.html",
        )
