"""AI Root-Cause Diagnosis using Gemini 3.8 Flash (`google-genai` Interactions API) with heuristic fallback."""

from __future__ import annotations

import logging

from src.checker import CheckResult
from src.config import MonitorConfig

logger = logging.getLogger(__name__)

SRE_SYSTEM_INSTRUCTION = (
    "You are a Staff Site Reliability Engineer diagnosing a Render web service outage. "
    "Respond in EXACTLY 3 concise lines (no markdown headers, no extra commentary):\n"
    "Line 1 - Root Cause: <1-sentence root cause based on logs/status>\n"
    "Line 2 - Evidence: <key error message, signal, or HTTP code from logs>\n"
    "Line 3 - Fix: <concrete, immediate fix for the developer>"
)


def _heuristic_fallback_diagnosis(check: CheckResult, log_lines: list[str]) -> str:
    """Deterministic log pattern analyzer used when GEMINI_API_KEY is unset or unreachable."""
    joined = "\n".join(log_lines).lower()

    if "out of memory" in joined or "oom" in joined or "sigkill" in joined or "worker with pid" in joined:
        return (
            "Root Cause: Container exceeded Render Free Tier 512MB RAM limit (SIGKILL / OOM).\n"
            "Evidence: Worker process terminated abruptly by OS memory killer.\n"
            "Fix: Reduce Gunicorn/Uvicorn workers to 1 (--workers 1) and lazy-load heavy ML models."
        )
    if "traceback (most recent call last)" in joined or "exception" in joined:
        last_err = next(
            (line for line in reversed(log_lines) if "error" in line.lower() or "exception" in line.lower()),
            check.error_reason,
        )
        return (
            "Root Cause: Unhandled runtime exception crashed the application request handler.\n"
            f"Evidence: {last_err[:120]}\n"
            "Fix: Inspect the stack trace in Render logs and wrap external dependency calls in try/except."
        )
    if "port" in joined and ("bind" in joined or "timeout" in joined):
        return (
            "Root Cause: Application failed to bind to Render's $PORT environment variable in time.\n"
            f"Evidence: {check.error_reason}\n"
            "Fix: Bind server to 0.0.0.0:$PORT and defer heavy initialization until after server startup."
        )
    if check.status_code in (502, 503, 504):
        return (
            f"Root Cause: Upstream application gateway timeout or worker hang (HTTP {check.status_code}).\n"
            f"Evidence: Health check failed after {check.attempts_used} attempts ({check.latency_ms}ms).\n"
            "Fix: Check blocking synchronous I/O in /health or increase worker timeout (--timeout 120)."
        )

    return (
        f"Root Cause: Service unreachable or failed health verification ({check.error_reason}).\n"
        f"Evidence: HTTP status={check.status_code}, latency={check.latency_ms}ms after {check.attempts_used} retries.\n"
        "Fix: Verify Render deployment status and ensure /health returns HTTP 200 with expected keyword."
    )


def diagnose_failure_with_gemini(
    config: MonitorConfig,
    check: CheckResult,
    log_lines: list[str],
) -> str:
    """
    Send the last ~100 Render log lines and telemetry to Gemini (`gemini-3.8-flash`)
    using the `google-genai` Interactions API and return a 3-line SRE summary.
    Works for both outage root-cause triage (DOWN) and on-demand health audits (/diagnose when UP).
    """
    if not config.gemini_api_key:
        logger.info("GEMINI_API_KEY not set; using built-in SRE heuristic diagnosis.")
        if check.is_up:
            return (
                f"Status: Service is HEALTHY (HTTP {check.status_code}, {check.latency_ms}ms latency).\n"
                f"Logs: Inspected {len(log_lines)} recent log lines; no fatal crashes detected.\n"
                "Recommendation: Keep 5-min heartbeat active to prevent Render free-tier spin-down."
            )
        return _heuristic_fallback_diagnosis(check, log_lines)

    recent_logs = "\n".join(log_lines[-100:]) if log_lines else "(No Render API logs available)"
    health_state = "HEALTHY (UP)" if check.is_up else "OUTAGE (DOWN)"
    sys_inst = (
        SRE_SYSTEM_INSTRUCTION
        if not check.is_up
        else (
            "You are a Staff Site Reliability Engineer auditing a live Render web service. "
            "Respond in EXACTLY 3 concise lines (no markdown headers, no extra commentary):\n"
            "Line 1 - Health Summary: <1-sentence assessment of current status & latency>\n"
            "Line 2 - Log Findings: <key observation from the recent container logs, e.g. spin-downs, warnings, or clean startup>\n"
            "Line 3 - Recommendation: <1 actionable SRE tip based on the logs>"
        )
    )
    prompt = (
        f"Service Name: {config.app_name}\n"
        f"Target URL: {config.target_url}\n"
        f"Current State: {health_state}\n"
        f"HTTP Status Code: {check.status_code}\n"
        f"Error Reason: {check.error_reason or 'None'}\n"
        f"Latency: {check.latency_ms} ms\n\n"
        f"--- LAST 100 RENDER LOG LINES ---\n{recent_logs}\n"
        "--- END LOGS ---\n\n"
        "Provide your 3-line SRE summary:"
    )

    try:
        from google import genai

        client = genai.Client(api_key=config.gemini_api_key)
        model_name = config.gemini_model or "gemini-3.8-flash"
        interaction = client.interactions.create(
            model=model_name,
            system_instruction=sys_inst,
            input=prompt,
            store=False,
        )
        output = (interaction.output_text or "").strip()
        if output:
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            return "\n".join(lines[:3])
    except Exception as exc:
        logger.warning("Gemini API diagnosis failed (%s); falling back to heuristics.", exc)

    if check.is_up:
        return (
            f"Health Summary: Service is UP and responding in {check.latency_ms}ms (HTTP {check.status_code}).\n"
            f"Log Findings: Analyzed {len(log_lines)} recent Render log entries.\n"
            "Recommendation: No immediate action needed; service is operating normally."
        )
    return _heuristic_fallback_diagnosis(check, log_lines)
