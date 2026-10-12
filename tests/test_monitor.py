"""Unit tests for Render SRE Agent state machine, retry logic, self-healing rate limits, and dashboard."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ai_diagnoser import diagnose_failure_with_gemini
from src.checker import CheckResult, perform_health_check
from src.config import IST, MonitorConfig
from src.dashboard import compute_metrics, generate_dashboard_html
from src.state_manager import (
    AgentState,
    append_and_prune_csv,
    evaluate_state_transition,
)


def _make_check(
    is_up: bool = True,
    status_code: int = 200,
    latency_ms: int = 120,
    is_slow: bool = False,
    is_cold_start: bool = False,
    ssl_days: int | None = 60,
    ssl_warning: bool = False,
    epoch_sec: float = 1760000000.0,
    error_reason: str = "",
) -> CheckResult:
    dt_str = datetime.fromtimestamp(epoch_sec, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")
    return CheckResult(
        timestamp_ist=dt_str,
        epoch_sec=epoch_sec,
        is_up=is_up,
        status_code=status_code,
        latency_ms=latency_ms,
        is_cold_start=is_cold_start,
        is_slow=is_slow,
        keyword_matched=is_up,
        attempts_used=1 if is_up else 3,
        ssl_days_remaining=ssl_days,
        ssl_warning=ssl_warning,
        error_reason=error_reason,
    )


class TestStateMachineAndSelfHealing(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.config = MonitorConfig.from_env(base_dir=self.base_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_up_to_down_and_deduplication_and_self_heal_on_third_failure(self) -> None:
        state = AgentState()
        t0 = 1760000000.0

        # Failure #1: UP -> DOWN alerts immediately, opens incident, no self-heal yet
        c1 = _make_check(is_up=False, status_code=502, epoch_sec=t0, error_reason="HTTP 502")
        a1 = evaluate_state_transition(state, c1, self.config)
        self.assertEqual(a1.transition_type, "UP_TO_DOWN")
        self.assertTrue(a1.alert_down)
        self.assertFalse(a1.should_self_heal)
        self.assertEqual(state.current_status, "DOWN")
        self.assertEqual(state.consecutive_failures, 1)
        self.assertEqual(len(state.incidents), 1)

        # Failure #2: Stays DOWN, NO duplicate alert_down, no self-heal yet
        c2 = _make_check(is_up=False, status_code=502, epoch_sec=t0 + 600, error_reason="HTTP 502")
        a2 = evaluate_state_transition(state, c2, self.config)
        self.assertIsNone(a2.transition_type)
        self.assertFalse(a2.alert_down)
        self.assertFalse(a2.should_self_heal)
        self.assertEqual(state.consecutive_failures, 2)

        # Failure #3: Reaches 3 consecutive failures -> triggers self-healing!
        c3 = _make_check(is_up=False, status_code=502, epoch_sec=t0 + 1200, error_reason="HTTP 502")
        a3 = evaluate_state_transition(state, c3, self.config)
        self.assertFalse(a3.alert_down)
        self.assertTrue(a3.should_self_heal)
        self.assertFalse(a3.heal_rate_limited)
        self.assertEqual(state.consecutive_failures, 3)

    def test_self_healing_rate_limited_at_two_per_hour(self) -> None:
        t0 = 1760000000.0
        state = AgentState(
            current_status="DOWN",
            consecutive_failures=2,
            restart_timestamps=[t0 - 1800, t0 - 600],  # 2 restarts in last 30 mins
        )

        # 3rd failure while 2 restarts already used in the last hour -> rate limited
        c3 = _make_check(is_up=False, status_code=503, epoch_sec=t0, error_reason="HTTP 503")
        actions = evaluate_state_transition(state, c3, self.config)
        self.assertFalse(actions.should_self_heal)
        self.assertTrue(actions.heal_rate_limited)
        self.assertEqual(actions.restarts_in_last_hour, 2)

        # Advance time by 3700 seconds (>1 hour) -> old restarts expire, self-heal allowed again
        c4 = _make_check(is_up=False, status_code=503, epoch_sec=t0 + 3700, error_reason="HTTP 503")
        actions_later = evaluate_state_transition(state, c4, self.config)
        self.assertTrue(actions_later.should_self_heal)
        self.assertFalse(actions_later.heal_rate_limited)
        self.assertEqual(actions_later.restarts_in_last_hour, 0)

    def test_down_to_up_recovery_closes_incident(self) -> None:
        t0 = 1760000000.0
        state = AgentState()

        # Go DOWN
        c_down = _make_check(is_up=False, status_code=500, epoch_sec=t0, error_reason="HTTP 500")
        evaluate_state_transition(state, c_down, self.config)

        # Recover 420 seconds later
        c_up = _make_check(is_up=True, status_code=200, latency_ms=95, epoch_sec=t0 + 420)
        actions = evaluate_state_transition(state, c_up, self.config)

        self.assertEqual(actions.transition_type, "DOWN_TO_UP")
        self.assertTrue(actions.alert_recovered)
        self.assertEqual(actions.outage_duration_sec, 420)
        self.assertEqual(state.current_status, "UP")
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.incidents[0]["duration_sec"], 420)
        self.assertNotEqual(state.incidents[0]["end_time_ist"], "Ongoing")

    def test_consecutive_slow_checks_alert_once_and_reset(self) -> None:
        state = AgentState()
        t0 = 1760000000.0

        # Slow check 1 & 2 -> no alert yet
        for i in range(1, 3):
            chk = _make_check(is_up=True, latency_ms=6200, is_slow=True, epoch_sec=t0 + i * 60)
            act = evaluate_state_transition(state, chk, self.config)
            self.assertFalse(act.alert_slow)

        # Slow check 3 -> triggers alert_slow
        chk3 = _make_check(is_up=True, latency_ms=7100, is_slow=True, epoch_sec=t0 + 180)
        act3 = evaluate_state_transition(state, chk3, self.config)
        self.assertTrue(act3.alert_slow)
        self.assertTrue(state.slow_alert_active)

        # Slow check 4 -> suppressed (already alerted)
        chk4 = _make_check(is_up=True, latency_ms=6800, is_slow=True, epoch_sec=t0 + 240)
        act4 = evaluate_state_transition(state, chk4, self.config)
        self.assertFalse(act4.alert_slow)

        # Fast check -> resets slow counter & active flag
        chk_fast = _make_check(is_up=True, latency_ms=150, is_slow=False, epoch_sec=t0 + 300)
        evaluate_state_transition(state, chk_fast, self.config)
        self.assertEqual(state.consecutive_slow, 0)
        self.assertFalse(state.slow_alert_active)

    def test_ssl_warning_and_daily_summary_deduplication(self) -> None:
        state = AgentState()
        # 2026-10-12 09:15:00 IST
        morning_ist = datetime(2026, 10, 12, 9, 15, 0, tzinfo=IST)
        chk = _make_check(
            is_up=True,
            ssl_days=9,
            ssl_warning=True,
            epoch_sec=morning_ist.timestamp(),
        )

        act1 = evaluate_state_transition(state, chk, self.config, now_dt=morning_ist)
        self.assertTrue(act1.alert_ssl)
        self.assertTrue(act1.should_send_daily_summary)

        # Next check 10 mins later on same day -> neither SSL nor Daily Summary re-fires
        act2 = evaluate_state_transition(state, chk, self.config, now_dt=morning_ist)
        self.assertFalse(act2.alert_ssl)
        self.assertFalse(act2.should_send_daily_summary)

    @patch("src.checker.check_ssl_expiry", return_value=(45, ""))
    @patch("src.checker.requests.get")
    def test_retry_with_backoff_recovers_on_third_attempt(
        self, mock_get: MagicMock, _mock_ssl: MagicMock
    ) -> None:
        fail_resp = MagicMock(status_code=502, text="Bad Gateway")
        ok_resp = MagicMock(status_code=200, text='{"status": "ok", "version": "1.2.0"}')
        ok_resp.json.return_value = {"status": "ok", "version": "1.2.0"}
        mock_get.side_effect = [fail_resp, fail_resp, ok_resp]

        result = perform_health_check(self.config, sleep_fn=lambda _: None)
        self.assertTrue(result.is_up)
        self.assertEqual(result.attempts_used, 3)
        self.assertEqual(result.app_version, "1.2.0")

    def test_ai_heuristic_fallback_and_dashboard_generation(self) -> None:
        from dataclasses import replace

        state = AgentState()
        down_check = _make_check(is_up=False, status_code=502, error_reason="HTTP 502")
        logs = [
            "[2026-10-12T15:00:01] [CRITICAL] WORKER TIMEOUT (pid:54)",
            "[2026-10-12T15:00:02] Worker with pid 54 was terminated due to signal 9 (SIGKILL) Out of memory",
        ]
        no_key_cfg = replace(self.config, gemini_api_key="")
        diagnosis = diagnose_failure_with_gemini(no_key_cfg, down_check, logs)
        self.assertIn("512MB RAM", diagnosis)
        self.assertEqual(len(diagnosis.splitlines()), 3)

        rows = append_and_prune_csv(self.config.checks_csv_path, down_check)
        metrics = compute_metrics(rows, state, down_check.epoch_sec)
        html_out = generate_dashboard_html(self.config, rows, state, metrics)
        self.assertTrue(self.config.dashboard_html_path.exists())
        self.assertIn("Render Web Service", html_out)


if __name__ == "__main__":
    unittest.main()
