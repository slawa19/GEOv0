from __future__ import annotations

import contextvars
import re
import uuid

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


# X-Request-ID validation
# - Keep it simple and strict to reduce log/trace injection risk.
# - Allow ASCII only and a conservative charset.
# - Length limit is enforced by both the explicit check and the regex quantifier.
_REQUEST_ID_ALLOWED_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$", flags=re.ASCII)


def validate_request_id(value: str | None) -> str | None:
    """Return value if it is a safe/valid request id, otherwise None.

    Policy:
    - ASCII only
    - length 1..64
    - charset: [A-Za-z0-9._-]
    """

    if value is None:
        return None
    if not isinstance(value, str):
        return None
    # Fast fail on extreme lengths before regex.
    if len(value) < 1 or len(value) > 64:
        return None
    if _REQUEST_ID_ALLOWED_RE.fullmatch(value) is None:
        return None
    return value


def new_request_id() -> str:
    return uuid.uuid4().hex
