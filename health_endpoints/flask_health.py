"""
Drop-in /health Blueprint for Flask applications on Render.

Usage in your Flask app (app.py):
    from flask_health import health_bp
    app.register_blueprint(health_bp)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)
START_TIME = time.monotonic()
APP_VERSION = os.getenv("APP_VERSION", os.getenv("RENDER_GIT_COMMIT", "1.0.0")[:7])


@health_bp.route("/health", methods=["GET"])
def health_check():
    """Fast, zero-cost health check endpoint."""
    return (
        jsonify(
            {
                "status": "ok",
                "uptime_seconds": int(time.monotonic() - START_TIME),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "version": APP_VERSION,
                "checks": {"app_runtime": "ready"},
            }
        ),
        200,
    )
