"""Main entrypoint for the Render Self-Healing & AI Monitor Agent."""

from __future__ import annotations

import logging
import sys

from src.ai_diagnoser import diagnose_failure_with_gemini
from src.alerter import (
    dispatch_alert,
    format_daily_summary,
    format_down_alert,
    format_recovery_alert,
    format_self_heal_alert,
    format_slow_alert,
    format_ssl_alert,
)
from src.checker import perform_health_check
from src.config import MonitorConfig
from src.dashboard import compute_metrics, generate_dashboard_html
from src.render_client import fetch_render_logs, restart_and_verify_service
from src.state_manager import (
    AgentState,
    append_and_prune_csv,
    evaluate_state_transition,
)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_monitor_cycle(config: MonitorConfig | None = None) -> int:
    """Execute one complete check, self-heal, alert, and dashboard generation cycle."""
    cfg = config or MonitorConfig.from_env()
    logger = logging.getLogger("RenderSREAgent")

    logger.info("Starting monitor cycle for %s (%s)", cfg.app_name, cfg.target_url)

    # 1. Load previous persistent state
    state = AgentState.load(cfg.state_json_path)

    # 2. Execute HTTP + Keyword + SSL check (with 3x retry & backoff)
    check = perform_health_check(cfg)

    # 3. Evaluate state machine transitions
    actions = evaluate_state_transition(state, check, cfg)

    ai_diagnosis = ""
    if actions.alert_down or actions.should_self_heal:
        logger.info("Fetching Render logs and running Gemini 3.8 Flash root-cause diagnosis...")
        logs = fetch_render_logs(cfg, limit=100)
        ai_diagnosis = diagnose_failure_with_gemini(cfg, check, logs)
        if state.incidents:
            state.incidents[0]["ai_diagnosis"] = ai_diagnosis

    # 4. Dispatch UP -> DOWN alert
    if actions.alert_down:
        subj, msg = format_down_alert(cfg, check, ai_diagnosis)
        dispatch_alert(cfg, subj, msg)

    # 5. Execute Self-Healing if 3 consecutive failures reached (and under 2/hr cap)
    if actions.should_self_heal:
        logger.warning(
            "Triggering Render container self-heal (consecutive_failures=%d, restarts_last_hr=%d/%d)...",
            state.consecutive_failures,
            actions.restarts_in_last_hour,
            cfg.max_restarts_per_hour,
        )
        outcome = restart_and_verify_service(cfg)
        if state.incidents:
            state.incidents[0]["auto_restart_attempted"] = True
            state.incidents[0]["auto_restart_succeeded"] = outcome.recovered

        if outcome.triggered:
            state.restart_timestamps.append(check.epoch_sec)
            restarts_used = len(state.restart_timestamps)

            if outcome.recovered and outcome.verification_check is not None:
                # Immediately mark service recovered after self-heal
                state.current_status = "UP"
                state.consecutive_failures = 0
                if state.incidents and state.incidents[0].get("end_time_ist") == "Ongoing":
                    start_ep = float(state.incidents[0].get("start_epoch", check.epoch_sec))
                    state.incidents[0]["end_time_ist"] = outcome.verification_check.timestamp_ist
                    state.incidents[0]["duration_sec"] = max(
                        0, int(outcome.verification_check.epoch_sec - start_ep)
                    )
                check = outcome.verification_check
        else:
            restarts_used = len(state.restart_timestamps)

        subj, msg = format_self_heal_alert(cfg, outcome, restarts_used, ai_diagnosis)
        dispatch_alert(cfg, subj, msg)

    elif actions.heal_rate_limited:
        logger.warning(
            "Self-heal suppressed: rate limit of %d restarts/hour reached.",
            cfg.max_restarts_per_hour,
        )

    # 6. Dispatch DOWN -> UP recovery alert
    if actions.alert_recovered:
        subj, msg = format_recovery_alert(cfg, check, actions.outage_duration_sec)
        dispatch_alert(cfg, subj, msg)

    # 7. Dispatch consecutive slow response alert (>5s x 3)
    if actions.alert_slow:
        subj, msg = format_slow_alert(cfg, check, state.consecutive_slow)
        dispatch_alert(cfg, subj, msg)

    # 8. Dispatch SSL expiry warning (<14 days)
    if actions.alert_ssl:
        subj, msg = format_ssl_alert(cfg, check)
        dispatch_alert(cfg, subj, msg)

    # 9. Persist CSV telemetry (auto-pruned to 30 days) and build static dashboard
    rows = append_and_prune_csv(cfg.checks_csv_path, check)
    metrics = compute_metrics(rows, state, check.epoch_sec)
    generate_dashboard_html(cfg, rows, state, metrics)

    # 10. Dispatch Daily 09:00 IST Summary
    if actions.should_send_daily_summary:
        subj, msg = format_daily_summary(cfg, metrics, check.timestamp_ist)
        dispatch_alert(cfg, subj, msg)

    # 11. Process any incoming Telegram Bot commands (/status, /logs, /diagnose, /restart)
    from src.telegram_commands import process_telegram_commands

    process_telegram_commands(cfg, state, check, metrics)

    # 12. Save updated state.json
    state.save(cfg.state_json_path)
    logger.info(
        "Cycle complete | status=%s | latency=%dms | uptime_24h=%s%%",
        state.current_status,
        check.latency_ms,
        metrics["uptime_24h"],
    )
    return 0


if __name__ == "__main__":
    setup_logging()
    sys.exit(run_monitor_cycle())
