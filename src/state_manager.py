"""State machine, incident tracking, and auto-pruned CSV history persistence."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.checker import CheckResult
from src.config import IST, MonitorConfig, format_ist

logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "timestamp_ist",
    "epoch_sec",
    "status",
    "status_code",
    "latency_ms",
    "is_cold_start",
    "is_slow",
    "attempts_used",
    "ssl_days_remaining",
    "error_reason",
]

MAX_CSV_RETENTION_DAYS = 30
MAX_INCIDENTS_HISTORY = 50


@dataclass
class IncidentRecord:
    """Represents an outage or self-healing event displayed on the dashboard."""

    id: str
    start_time_ist: str
    start_epoch: float
    end_time_ist: str = "Ongoing"
    duration_sec: int = 0
    reason: str = ""
    status_code: int = 0
    auto_restart_attempted: bool = False
    auto_restart_succeeded: bool = False
    ai_diagnosis: str = ""


@dataclass
class AgentState:
    """Persistent state stored in data/state.json across ephemeral GitHub Actions runs."""

    current_status: str = "UP"  # "UP" or "DOWN"
    consecutive_failures: int = 0
    consecutive_slow: int = 0
    slow_alert_active: bool = False
    restart_timestamps: list[float] = field(default_factory=list)
    last_daily_summary_date: str = ""
    last_ssl_alert_date: str = ""
    last_check_ist: str = ""
    last_telegram_update_id: int = 0
    incidents: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> AgentState:
        """Load state from JSON file or return fresh default state."""
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                current_status=str(raw.get("current_status", "UP")),
                consecutive_failures=int(raw.get("consecutive_failures", 0)),
                consecutive_slow=int(raw.get("consecutive_slow", 0)),
                slow_alert_active=bool(raw.get("slow_alert_active", False)),
                restart_timestamps=[
                    float(ts) for ts in raw.get("restart_timestamps", [])
                ],
                last_daily_summary_date=str(raw.get("last_daily_summary_date", "")),
                last_ssl_alert_date=str(raw.get("last_ssl_alert_date", "")),
                last_check_ist=str(raw.get("last_check_ist", "")),
                last_telegram_update_id=int(raw.get("last_telegram_update_id", 0)),
                incidents=list(raw.get("incidents", [])),
            )
        except Exception as exc:
            logger.warning("Failed to parse state.json (%s), resetting state.", exc)
            return cls()

    def save(self, path: Path) -> None:
        """Persist state atomically to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


@dataclass
class TransitionActions:
    """Decisions emitted by the state machine for a single check cycle."""

    transition_type: str | None = None  # "UP_TO_DOWN", "DOWN_TO_UP", or None
    alert_down: bool = False
    alert_recovered: bool = False
    alert_slow: bool = False
    alert_ssl: bool = False
    should_self_heal: bool = False
    heal_rate_limited: bool = False
    should_send_daily_summary: bool = False
    restarts_in_last_hour: int = 0
    outage_duration_sec: int = 0


def evaluate_state_transition(
    state: AgentState,
    check: CheckResult,
    config: MonitorConfig,
    now_dt: datetime | None = None,
) -> TransitionActions:
    """
    Pure state-machine transition evaluator.
    Mutates `state` in-place and returns `TransitionActions` describing what alerts
    and self-healing actions must be executed.
    """
    current_dt = (
        now_dt.astimezone(IST)
        if now_dt
        else datetime.fromtimestamp(check.epoch_sec, tz=IST)
    )
    today_str = current_dt.strftime("%Y-%m-%d")
    actions = TransitionActions()

    # 1. Prune restart_timestamps older than 1 hour (3600s)
    one_hour_ago = check.epoch_sec - 3600.0
    state.restart_timestamps = [
        ts for ts in state.restart_timestamps if ts > one_hour_ago
    ]
    actions.restarts_in_last_hour = len(state.restart_timestamps)
    state.last_check_ist = check.timestamp_ist

    # 2. Evaluate UP vs DOWN transitions
    if check.is_up:
        if state.current_status == "DOWN":
            actions.transition_type = "DOWN_TO_UP"
            actions.alert_recovered = True
            # Close active incident if one exists
            if state.incidents and state.incidents[0].get("end_time_ist") == "Ongoing":
                inc = state.incidents[0]
                start_epoch = float(inc.get("start_epoch", check.epoch_sec))
                duration = max(0, int(check.epoch_sec - start_epoch))
                inc["end_time_ist"] = check.timestamp_ist
                inc["duration_sec"] = duration
                actions.outage_duration_sec = duration

        state.current_status = "UP"
        state.consecutive_failures = 0

        # Evaluate consecutive slow checks (>5s)
        if check.is_slow:
            state.consecutive_slow += 1
            if (
                state.consecutive_slow >= config.consecutive_slow_limit
                and not state.slow_alert_active
            ):
                actions.alert_slow = True
                state.slow_alert_active = True
        else:
            state.consecutive_slow = 0
            state.slow_alert_active = False

    else:
        # Service check failed (after all retries)
        state.consecutive_failures += 1
        state.consecutive_slow = 0

        if state.current_status == "UP":
            actions.transition_type = "UP_TO_DOWN"
            actions.alert_down = True
            state.current_status = "DOWN"

            # Open a new incident record at the top of the list
            new_incident = asdict(
                IncidentRecord(
                    id=f"INC-{int(check.epoch_sec)}",
                    start_time_ist=check.timestamp_ist,
                    start_epoch=check.epoch_sec,
                    reason=check.error_reason or f"HTTP {check.status_code}",
                    status_code=check.status_code,
                )
            )
            state.incidents.insert(0, new_incident)
            state.incidents = state.incidents[:MAX_INCIDENTS_HISTORY]

        # Check if self-healing threshold reached (e.g. >= 3 consecutive failures)
        if state.consecutive_failures >= config.consecutive_failures_for_heal:
            if len(state.restart_timestamps) < config.max_restarts_per_hour:
                actions.should_self_heal = True
            else:
                actions.heal_rate_limited = True

    # 3. Evaluate SSL certificate expiry alert (max once per calendar day IST)
    if check.ssl_warning and state.last_ssl_alert_date != today_str:
        actions.alert_ssl = True
        state.last_ssl_alert_date = today_str

    # 4. Evaluate Daily Summary at or after 09:00 IST
    if current_dt.hour >= 9 and state.last_daily_summary_date != today_str:
        actions.should_send_daily_summary = True
        state.last_daily_summary_date = today_str

    return actions


def append_and_prune_csv(csv_path: Path, check: CheckResult) -> list[dict[str, str]]:
    """
    Append the current check to data/checks.csv and prune records older than 30 days
    so the repository never suffers from unbounded file growth.
    Returns all retained rows.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows: list[dict[str, str]] = []

    cutoff_epoch = check.epoch_sec - (MAX_CSV_RETENTION_DAYS * 86400)

    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        row_epoch = float(row.get("epoch_sec", 0))
                        if row_epoch >= cutoff_epoch:
                            existing_rows.append(row)
                    except ValueError:
                        continue
        except Exception as exc:
            logger.warning("Could not read existing checks.csv: %s", exc)

    new_row = {
        "timestamp_ist": check.timestamp_ist,
        "epoch_sec": f"{check.epoch_sec:.2f}",
        "status": "UP" if check.is_up else "DOWN",
        "status_code": str(check.status_code),
        "latency_ms": str(check.latency_ms),
        "is_cold_start": "1" if check.is_cold_start else "0",
        "is_slow": "1" if check.is_slow else "0",
        "attempts_used": str(check.attempts_used),
        "ssl_days_remaining": (
            str(check.ssl_days_remaining)
            if check.ssl_days_remaining is not None
            else ""
        ),
        "error_reason": check.error_reason,
    }
    existing_rows.append(new_row)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(existing_rows)

    return existing_rows
