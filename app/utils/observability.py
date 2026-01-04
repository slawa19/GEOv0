from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator


@contextmanager
def log_duration(logger: Any, operation: str, **fields: object) -> Iterator[None]:
    """Log duration of an operation.

    Uses logger.debug with a stable key=value format to keep logs parseable even without
    a JSON logging formatter.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        try:
            from app.utils.request_id import request_id_var

            rid = request_id_var.get()
            if rid and "request_id" not in fields:
                fields = {"request_id": rid, **fields}
        except Exception:
            # Best-effort: observability must not break business logic.
            pass
        extras = " ".join(f"{k}={v}" for k, v in fields.items())
        if extras:
            logger.debug("op=%s duration_ms=%.2f %s", operation, elapsed_ms, extras)
        else:
            logger.debug("op=%s duration_ms=%.2f", operation, elapsed_ms)
