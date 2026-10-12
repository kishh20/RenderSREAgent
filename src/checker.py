"""HTTP health, keyword verification, cold-start detection, and SSL certificate checker."""

from __future__ import annotations

import logging
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from src.config import MonitorConfig, format_ist, now_ist

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Outcome of a single health check cycle (including retries and SSL inspection)."""

    timestamp_ist: str
    epoch_sec: float
    is_up: bool
    status_code: int
    latency_ms: int
    is_cold_start: bool
    is_slow: bool
    keyword_matched: bool
    attempts_used: int
    ssl_days_remaining: int | None
    ssl_warning: bool
    error_reason: str = ""
    app_version: str = ""


def check_ssl_expiry(url: str, timeout_sec: int = 10) -> tuple[int | None, str]:
    """
    Inspect the TLS/SSL certificate of an HTTPS endpoint and return days until expiry.

    Returns:
        (days_remaining, error_message)
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None, ""

    hostname = parsed.hostname
    port = parsed.port or 443
    context = ssl.create_default_context()

    try:
        with socket.create_connection((hostname, port), timeout=timeout_sec) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                if not cert or "notAfter" not in cert:
                    return None, "Certificate missing notAfter field"

                not_after_str = str(cert["notAfter"])
                expiry_dt = datetime.strptime(
                    not_after_str, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
                now_utc = datetime.now(tz=timezone.utc)
                remaining_days = (expiry_dt - now_utc).days
                return remaining_days, ""
    except Exception as exc:
        logger.warning("SSL certificate check failed for %s: %s", hostname, exc)
        return None, f"SSL Error: {exc}"


def perform_health_check(
    config: MonitorConfig,
    sleep_fn=time.sleep,
) -> CheckResult:
    """
    Execute health check with 3x exponential backoff retry, keyword verification,
    cold-start detection, and SSL certificate expiry inspection.
    """
    check_time = now_ist()
    ts_str = format_ist(check_time)
    epoch_sec = check_time.timestamp()

    ssl_days, ssl_err = check_ssl_expiry(config.target_url)
    ssl_warning = (
        ssl_days is not None and ssl_days < config.ssl_expiry_warn_days
    )

    last_status_code = 0
    last_latency_ms = 0
    last_error = ""
    keyword_matched = False
    app_version = ""

    headers = {
        "User-Agent": "RenderSREAgent/2.0 (+https://github.com/RenderSREAgent)",
        "Accept": "application/json, text/plain, */*",
        "Cache-Control": "no-cache",
    }

    total_attempts = max(1, config.max_retries)

    for attempt in range(1, total_attempts + 1):
        start_perf = time.perf_counter()
        try:
            response = requests.get(
                config.target_url,
                headers=headers,
                timeout=config.request_timeout_sec,
            )
            elapsed_ms = int((time.perf_counter() - start_perf) * 1000)
            last_status_code = response.status_code
            last_latency_ms = elapsed_ms

            body_text = response.text or ""
            keyword_matched = (
                not config.expected_keyword
                or config.expected_keyword.lower() in body_text.lower()
            )

            # Extract optional app_version if JSON response provides it
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    app_version = str(payload.get("version", ""))
            except ValueError:
                pass

            if response.status_code == 200 and keyword_matched:
                is_cold = elapsed_ms > config.cold_start_threshold_ms
                is_slow = elapsed_ms > config.slow_threshold_ms
                logger.info(
                    "Check UP on attempt %d/%d | status=%d | latency=%dms | cold_start=%s",
                    attempt,
                    total_attempts,
                    response.status_code,
                    elapsed_ms,
                    is_cold,
                )
                return CheckResult(
                    timestamp_ist=ts_str,
                    epoch_sec=epoch_sec,
                    is_up=True,
                    status_code=response.status_code,
                    latency_ms=elapsed_ms,
                    is_cold_start=is_cold,
                    is_slow=is_slow,
                    keyword_matched=True,
                    attempts_used=attempt,
                    ssl_days_remaining=ssl_days,
                    ssl_warning=ssl_warning,
                    error_reason=ssl_err if ssl_warning else "",
                    app_version=app_version,
                )

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
            elif not keyword_matched:
                last_error = (
                    f"Missing keyword '{config.expected_keyword}' in 200 response body"
                )

        except requests.Timeout:
            elapsed_ms = int((time.perf_counter() - start_perf) * 1000)
            last_latency_ms = elapsed_ms
            last_status_code = 0
            last_error = f"Timeout after {config.request_timeout_sec}s"
        except requests.RequestException as exc:
            elapsed_ms = int((time.perf_counter() - start_perf) * 1000)
            last_latency_ms = elapsed_ms
            last_status_code = 0
            last_error = f"Connection error: {exc.__class__.__name__}"

        if attempt < total_attempts:
            backoff_sec = config.retry_Backoff_base_sec * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed (%s). Retrying in %.1fs...",
                attempt,
                total_attempts,
                last_error,
                backoff_sec,
            )
            sleep_fn(backoff_sec)

    logger.error(
        "Service declared DOWN after %d attempts. Last error: %s",
        total_attempts,
        last_error,
    )
    return CheckResult(
        timestamp_ist=ts_str,
        epoch_sec=epoch_sec,
        is_up=False,
        status_code=last_status_code,
        latency_ms=last_latency_ms,
        is_cold_start=last_latency_ms > config.cold_start_threshold_ms,
        is_slow=last_latency_ms > config.slow_threshold_ms,
        keyword_matched=keyword_matched,
        attempts_used=total_attempts,
        ssl_days_remaining=ssl_days,
        ssl_warning=ssl_warning,
        error_reason=last_error,
        app_version=app_version,
    )
