"""
Drop-in /health router for FastAPI applications on Render.
Fast (<2ms), calls zero paid external APIs, and reports memory usage (vital for Render 512MB limit).

Usage in your FastAPI app (main.py):
    from health_router import router as health_router
    app.include_router(health_router)
"""

from __future__ import annotations

import os
import resource
import sys
import time
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["Health"])
START_TIME = time.monotonic()
APP_VERSION = os.getenv("APP_VERSION", os.getenv("RENDER_GIT_COMMIT", "1.0.0")[:7])


def _get_memory_mb() -> float:
    """Return current process RSS memory in MB (Linux/macOS via resource or /proc/self/statm)."""
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as f:
            rss_pages = int(f.read().split()[1])
            return round((rss_pages * os.sysconf("SC_PAGE_SIZE")) / (1024 * 1024), 2)
    except Exception:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = (1024 * 1024) if sys.platform == "darwin" else 1024
        return round(usage / divisor, 2)


@router.get("/health")
async def health_check() -> dict:
    """Lightweight health check endpoint for Render SRE Monitor & cron-job.org."""
    uptime_sec = int(time.monotonic() - START_TIME)
    mem_mb = _get_memory_mb()

    # Replace or extend with fast local checks (e.g. ML model loaded in memory, DB pool initialized)
    dependencies = {
        "runtime": "ready",
        "memory_headroom": "ok" if mem_mb < 460 else "warning_near_512mb_limit",
    }

    return {
        "status": "ok",
        "uptime_seconds": uptime_sec,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "version": APP_VERSION,
        "memory_mb": mem_mb,
        "checks": dependencies,
    }
