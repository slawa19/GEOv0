from typing import Any, Dict, Optional

class GeoException(Exception):
    """Base exception for GEO application"""
    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 500
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        self.status_code = status_code
        super().__init__(self.message)

class BadRequestException(GeoException):
    def __init__(self, message: str, code: str = "BAD_REQUEST", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code, details, status_code=400)

class UnauthorizedException(GeoException):
    def __init__(self, message: str = "Unauthorized", code: str = "UNAUTHORIZED", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code, details, status_code=401)

class NotFoundException(GeoException):
    def __init__(self, message: str = "Not Found", code: str = "NOT_FOUND", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code, details, status_code=404)

class ForbiddenException(GeoException):
    def __init__(self, message: str = "Forbidden", code: str = "FORBIDDEN", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code, details, status_code=403)

class ConflictException(GeoException):
    def __init__(self, message: str, code: str = "CONFLICT", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code, details, status_code=409)

class CryptoException(GeoException):
    """Exceptions related to cryptographic operations"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="CRYPTO_ERROR", details=details, status_code=400)