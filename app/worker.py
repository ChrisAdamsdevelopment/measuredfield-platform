"""Dedicated PostgreSQL job worker. No live providers unless explicitly configured."""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

from .config import get_settings
from .db import isolation_self_check
from .services.jobs import run_due

logger = logging.getLogger("mf.worker")
_STOP = threading.Event()


def _stop(signum, frame) -> None:  # noqa: ARG001
    _STOP.set()


def startup_checks() -> None:
    settings = get_settings()
    problems = settings.validate_for_production()
    if settings.database_url:
        problems.extend(isolation_self_check())
    if problems and settings.is_production:
        raise RuntimeError("Worker refusing unsafe runtime configuration: " + " | ".join(problems))
    if not settings.database_url:
        raise RuntimeError("Worker requires DATABASE_URL")
    if settings.inline_jobs:
        raise RuntimeError("Worker requires MF_INLINE_JOBS=false")
    for problem in problems:
        logger.warning("worker startup check: %s", problem)


def main() -> int:
    logging.basicConfig(level=get_settings().log_level)
    startup_checks()
    poll_seconds = min(60.0, max(1.0, float(os.getenv("MF_WORKER_POLL_SECONDS", "3"))))
    batch = min(50, max(1, int(os.getenv("MF_WORKER_BATCH_SIZE", "15"))))
    for name in ("SIGTERM", "SIGINT"):
        value = getattr(signal, name, None)
        if value is not None:
            signal.signal(value, _stop)
    logger.info("MeasuredField worker ready")
    while not _STOP.is_set():
        try:
            processed = run_due(batch)
        except Exception:  # noqa: BLE001 - process survives transient DB outage
            logger.exception("Worker queue execution failed")
            processed = 0
        if processed == 0:
            _STOP.wait(poll_seconds)
    logger.info("MeasuredField worker exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
