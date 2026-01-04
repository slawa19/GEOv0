from __future__ import annotations

import contextvars
import uuid

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex
