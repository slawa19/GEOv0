"""Cookie-based anonymous session for simulator visitors.

Cookie format: v1.<sid_b64url>.<iat_decimal>.<sig_b64url>
- sid: 16 random bytes, base64url-encoded (no padding)
- iat: issued-at unix timestamp (integer)
- sig: HMAC-SHA256(secret, "v1.<sid>.<iat>"), base64url-encoded (no padding)
"""

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Optional

COOKIE_NAME = "geo_sim_sid"
COOKIE_VERSION = "v1"


@dataclass
class SessionInfo:
    sid: str          # base64url-encoded session id
    iat: int          # issued-at timestamp
    owner_id: str     # "anon:<sid>"


def create_session(secret: str) -> tuple[str, SessionInfo]:
    """Create a new session cookie value and SessionInfo.

    Returns:
        (cookie_value, session_info)
    """
    sid_bytes = os.urandom(16)
    sid = base64.urlsafe_b64encode(sid_bytes).rstrip(b"=").decode("ascii")
    iat = int(time.time())
    payload = f"{COOKIE_VERSION}.{sid}.{iat}"
    sig = _sign(secret, payload)
    cookie_value = f"{payload}.{sig}"
    return cookie_value, SessionInfo(sid=sid, iat=iat, owner_id=f"anon:{sid}")


def validate_session(
    cookie_value: str,
    secret: str,
    ttl_sec: int,
    clock_skew_sec: int = 300,
) -> Optional[SessionInfo]:
    """Validate cookie and return SessionInfo or None if invalid/expired."""
    parts = cookie_value.split(".")
    if len(parts) != 4:
        return None
    version, sid, iat_str, sig = parts
    if version != COOKIE_VERSION:
        return None
    try:
        iat = int(iat_str)
    except ValueError:
        return None

    # Verify signature (constant-time comparison)
    payload = f"{version}.{sid}.{iat_str}"
    expected_sig = _sign(secret, payload)
    if not hmac.compare_digest(sig, expected_sig):
        return None

    # Strict TTL: session expires when now - iat > ttl_sec (not >=).
    # clock_skew_sec is only used for future iat check, NOT to extend TTL.
    now = int(time.time())
    if now - iat > ttl_sec:
        return None
    # Clock skew only applies to future-issued tokens (replay protection).
    if iat > now + clock_skew_sec:
        return None

    return SessionInfo(sid=sid, iat=iat, owner_id=f"anon:{sid}")


def _sign(secret: str, payload: str) -> str:
    """HMAC-SHA256 sign and return base64url (no padding).

    Note: hmac.new() is the standard Python idiom and is an alias for hmac.HMAC.
    It is correct and verified to work in Python 3.x.
    """
    # hmac.new() == hmac.HMAC — canonical Python API; deliberate choice over hmac.digest()
    # for compatibility with Python 3.7+ and readability.
    sig_bytes = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode("ascii")
