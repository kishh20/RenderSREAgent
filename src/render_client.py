"""Render REST API v1 client for log retrieval and zero-build-minute container restarts."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests

from src.checker import CheckResult, perform_health_check
from src.config import MonitorConfig

logger = logging.getLogger(__name__)

RENDER_API_BASE = "https://api.render.com/v1"
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


@dataclass
class SelfHealOutcome:
    """Result of triggering a Render container restart and re-verifying health."""

    triggered: bool
    restart_api_status: int
    recovered: bool
    recovery_time_sec: int
    message: str
    verification_check: CheckResult | None = None


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def fetch_render_logs(config: MonitorConfig, limit: int = 100) -> list[str]:
    """
    Fetch the latest ~100 log lines from Render API v1.
    Resolves `ownerId` from `/v1/services/{serviceId}` first, then queries
    `/v1/logs?ownerId=...&resource={serviceId}&direction=backward&limit=100`.
    """
    if not config.render_api_key or not config.render_service_id:
        logger.info("Render API credentials not configured; skipping log fetch.")
        return []

    hdrs = _headers(config.render_api_key)
    service_id = config.render_service_id

    try:
        # Step 1: Retrieve service metadata to get ownerId
        svc_resp = requests.get(
            f"{RENDER_API_BASE}/services/{service_id}",
            headers=hdrs,
            timeout=15,
        )
        owner_id = ""
        if svc_resp.status_code == 200:
            svc_data = svc_resp.json()
            if isinstance(svc_data, dict):
                owner_id = str(
                    svc_data.get("ownerId")
                    or svc_data.get("service", {}).get("ownerId", "")
                )

        # Step 2: Query /v1/logs endpoint
        params: dict[str, str | int] = {
            "resource": service_id,
            "direction": "backward",
            "limit": limit,
        }
        if owner_id:
            params["ownerId"] = owner_id

        logs_resp = requests.get(
            f"{RENDER_API_BASE}/logs",
            headers=hdrs,
            params=params,
            timeout=20,
        )

        if logs_resp.status_code == 200:
            payload = logs_resp.json()
            raw_logs = (
                payload.get("logs", [])
                if isinstance(payload, dict)
                else (payload if isinstance(payload, list) else [])
            )
            cleaned_lines: list[str] = []
            for entry in raw_logs:
                if isinstance(entry, dict):
                    msg = str(entry.get("message", "")).strip()
                    ts = str(entry.get("timestamp", ""))[:19]
                    if msg:
                        clean_msg = ANSI_ESCAPE_RE.sub("", msg)
                        cleaned_lines.append(f"[{ts}] {clean_msg}" if ts else clean_msg)
                elif isinstance(entry, str):
                    cleaned_lines.append(ANSI_ESCAPE_RE.sub("", entry.strip()))

            if cleaned_lines:
                # Reverse backward logs into chronological order
                cleaned_lines.reverse()
                return cleaned_lines[-limit:]

        # Step 3: Fallback to service events if raw log stream is empty/restricted
        events_resp = requests.get(
            f"{RENDER_API_BASE}/services/{service_id}/events",
            headers=hdrs,
            params={"limit": 20},
            timeout=15,
        )
        if events_resp.status_code == 200:
            ev_payload = events_resp.json()
            events_list = ev_payload if isinstance(ev_payload, list) else []
            fallback_lines: list[str] = []
            for item in events_list:
                ev = item.get("event", item) if isinstance(item, dict) else {}
                ts = str(ev.get("timestamp", ""))[:19]
                ev_type = str(ev.get("type", "event"))
                details = ev.get("details", {})
                fallback_lines.append(f"[{ts}] Event={ev_type} Details={details}")
            return fallback_lines

        logger.warning(
            "Render logs endpoint returned HTTP %d: %s",
            logs_resp.status_code,
            logs_resp.text[:200],
        )
        return []

    except requests.RequestException as exc:
        logger.warning("Failed to fetch Render logs via API: %s", exc)
        return []


def restart_and_verify_service(
    config: MonitorConfig,
    sleep_fn=time.sleep,
    wait_before_verify_sec: int = 20,
) -> SelfHealOutcome:
    """
    Trigger a container restart via `POST /v1/services/{serviceId}/restart`
    (consumes 0 build minutes), wait for startup, and re-verify `/health`.
    """
    if not config.render_api_key or not config.render_service_id:
        return SelfHealOutcome(
            triggered=False,
            restart_api_status=0,
            recovered=False,
            recovery_time_sec=0,
            message="Skipped self-heal: RENDER_API_KEY or RENDER_SERVICE_ID not set.",
        )

    url = f"{RENDER_API_BASE}/services/{config.render_service_id}/restart"
    start_ts = time.perf_counter()

    try:
        resp = requests.post(
            url,
            headers=_headers(config.render_api_key),
            timeout=20,
        )
        if resp.status_code not in (200, 201, 202, 204):
            return SelfHealOutcome(
                triggered=False,
                restart_api_status=resp.status_code,
                recovered=False,
                recovery_time_sec=0,
                message=f"Render Restart API rejected request (HTTP {resp.status_code}: {resp.text[:120]})",
            )

        logger.info(
            "Render container restart triggered (HTTP %d). Waiting %ds before re-verification...",
            resp.status_code,
            wait_before_verify_sec,
        )
        sleep_fn(wait_before_verify_sec)

        verify_check = perform_health_check(config, sleep_fn=sleep_fn)
        total_sec = int(time.perf_counter() - start_ts)

        if verify_check.is_up:
            return SelfHealOutcome(
                triggered=True,
                restart_api_status=resp.status_code,
                recovered=True,
                recovery_time_sec=total_sec,
                message=f"Service RECOVERED in {total_sec}s after container restart (HTTP 200, {verify_check.latency_ms}ms).",
                verification_check=verify_check,
            )

        return SelfHealOutcome(
            triggered=True,
            restart_api_status=resp.status_code,
            recovered=False,
            recovery_time_sec=total_sec,
            message=f"Service STILL DOWN {total_sec}s after container restart ({verify_check.error_reason}).",
            verification_check=verify_check,
        )

    except requests.RequestException as exc:
        return SelfHealOutcome(
            triggered=False,
            restart_api_status=0,
            recovered=False,
            recovery_time_sec=0,
            message=f"Render Restart API network error: {exc}",
        )
