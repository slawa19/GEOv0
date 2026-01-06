from __future__ import annotations

from typing import Any, Optional

from app.utils.error_codes import ErrorCode, ERROR_MESSAGES


def _normalize_error_code(value: ErrorCode | str | None) -> ErrorCode:
    if value is None:
        return ErrorCode.E010
    if isinstance(value, ErrorCode):
        return value
    try:
        return ErrorCode(str(value))
    except Exception:
        return ErrorCode.E010


class GeoException(Exception):
    """Base exception for GEO application.

    API response format is handled by the global exception handler.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        code: ErrorCode | str = ErrorCode.E010,
        details: Optional[dict[str, Any]] = None,
        status_code: int = 500,
    ):
        normalized = _normalize_error_code(code)
        if message is None:
            message = ERROR_MESSAGES.get(normalized, ERROR_MESSAGES[ErrorCode.E010])

        self.message = message
        self.code = normalized.value
        self.details = details or {}
        self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class BadRequestException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=ErrorCode.E009, details=details, status_code=400)


class UnauthorizedException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message or "Unauthorized", code=ErrorCode.E006, details=details, status_code=401)


class NotFoundException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message or "Not Found", code=ErrorCode.E001, details=details, status_code=404)


class ForbiddenException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message or "Forbidden", code=ErrorCode.E006, details=details, status_code=403)


class ConflictException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=ErrorCode.E008, details=details, status_code=409)


class TooManyRequestsException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message or "Too many requests", code=ErrorCode.E009, details=details, status_code=429)


class InvalidSignatureException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=ErrorCode.E005, details=details, status_code=400)


class CryptoException(InvalidSignatureException):
    """Legacy name for signature/crypto errors."""


class TimeoutException(GeoException):
    def __init__(self, message: str | None = None, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=ErrorCode.E007, details=details, status_code=504)


class RoutingException(GeoException):
    def __init__(self, message: str | None = None, *, insufficient_capacity: bool = False, details: Optional[dict[str, Any]] = None):
        code = ErrorCode.E002 if insufficient_capacity else ErrorCode.E001
        super().__init__(message, code=code, details=details, status_code=400)


class TrustLineException(GeoException):
    def __init__(self, message: str | None = None, *, limit_exceeded: bool = False, details: Optional[dict[str, Any]] = None):
        code = ErrorCode.E003 if limit_exceeded else ErrorCode.E004
        super().__init__(message, code=code, details=details, status_code=400)


class IntegrityViolationException(GeoException):
    """Raised when a protocol or data integrity invariant is violated."""

    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=ErrorCode.E008, details=details, status_code=409)