"""Unit-тесты для app/core/simulator/session.py

Тестирует cookie-based anonymous session:
 - создание (create_session)
 - валидация (validate_session)
 - истечение (TTL)
 - подделанная подпись / sid
 - clock skew (iat в будущем)
 - неверный secret
 - плохой формат / неверная версия
"""
import base64
import time
from unittest.mock import patch

import pytest

from app.core.simulator.session import (
    COOKIE_NAME,
    COOKIE_VERSION,
    SessionInfo,
    _sign,
    create_session,
    validate_session,
)

# ─── Константы для тестов ────────────────────────────────────────────────────

SECRET = "test-secret-for-unit-tests"
TTL_SEC = 3600       # 1 час
CLOCK_SKEW = 300     # 5 минут


# ─── Вспомогательная функция ─────────────────────────────────────────────────

def _make_cookie_with_iat(secret: str, iat: int) -> str:
    """Создаёт валидную cookie с заданным iat (для тестирования TTL / clock skew).

    Использует публичный API _sign для корректной подписи.
    """
    import os
    sid_bytes = os.urandom(16)
    sid = base64.urlsafe_b64encode(sid_bytes).rstrip(b"=").decode("ascii")
    payload = f"{COOKIE_VERSION}.{sid}.{iat}"
    sig = _sign(secret, payload)
    return f"{payload}.{sig}"


# ─── Тесты ───────────────────────────────────────────────────────────────────

def test_create_session() -> None:
    """Создаёт валидную сессию; cookie_value начинается с 'v1.'"""
    cookie_value, info = create_session(SECRET)

    assert cookie_value.startswith("v1."), (
        f"cookie_value должен начинаться с 'v1.', получено: {cookie_value!r}"
    )
    assert isinstance(info, SessionInfo)
    # Формат: v1.<sid>.<iat>.<sig>  → 4 части
    parts = cookie_value.split(".")
    assert len(parts) == 4, f"Ожидается 4 части, получено {len(parts)}: {parts}"
    assert info.sid  # непустая строка
    assert info.iat > 0


def test_create_session_unique_sids() -> None:
    """Два вызова create_session дают разные sid."""
    _, info1 = create_session(SECRET)
    _, info2 = create_session(SECRET)

    assert info1.sid != info2.sid, "sid должны быть уникальными"


def test_validate_session_valid() -> None:
    """create_session → validate_session → SessionInfo (успех)."""
    cookie_value, created_info = create_session(SECRET)

    result = validate_session(cookie_value, SECRET, TTL_SEC, CLOCK_SKEW)

    assert result is not None, "Ожидается успешная валидация"
    assert result.sid == created_info.sid
    assert result.iat == created_info.iat
    assert result.owner_id == f"anon:{created_info.sid}"


def test_validate_session_tampered_signature() -> None:
    """Подмена sig → None (HMAC verify провалится)."""
    cookie_value, _ = create_session(SECRET)
    parts = cookie_value.split(".")  # [version, sid, iat, sig]
    # Заменяем последнюю часть (sig)
    parts[-1] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    tampered = ".".join(parts)

    result = validate_session(tampered, SECRET, TTL_SEC, CLOCK_SKEW)

    assert result is None, "Подделанная подпись должна вернуть None"


def test_validate_session_tampered_sid() -> None:
    """Подмена sid → None (sig больше не соответствует payload)."""
    cookie_value, _ = create_session(SECRET)
    parts = cookie_value.split(".")  # [version, sid, iat, sig]
    # Заменяем sid (индекс 1)
    parts[1] = "tampered-sid-AAAAAAAAAAAAA"
    tampered = ".".join(parts)

    result = validate_session(tampered, SECRET, TTL_SEC, CLOCK_SKEW)

    assert result is None, "Подделанный sid должен вернуть None"


def test_validate_session_expired() -> None:
    """iat в далёком прошлом, ttl_sec=60 → None (сессия истекла)."""
    # Создаём cookie с iat = 2 часа назад
    expired_iat = int(time.time()) - 7200
    cookie_value = _make_cookie_with_iat(SECRET, expired_iat)

    result = validate_session(cookie_value, SECRET, ttl_sec=60, clock_skew_sec=300)

    assert result is None, "Истёкшая сессия должна вернуть None"


def test_validate_session_future_clock_skew() -> None:
    """iat в будущем, но в пределах clock_skew=300 → SessionInfo (OK)."""
    # iat чуть в будущем (< clock_skew)
    slightly_future_iat = int(time.time()) + 200
    cookie_value = _make_cookie_with_iat(SECRET, slightly_future_iat)

    result = validate_session(cookie_value, SECRET, TTL_SEC, clock_skew_sec=300)

    assert result is not None, (
        "iat в пределах clock_skew должен быть допустим"
    )


def test_validate_session_too_far_future() -> None:
    """iat далеко в будущем (> clock_skew) → None."""
    far_future_iat = int(time.time()) + 1000  # +1000 сек > clock_skew=300
    cookie_value = _make_cookie_with_iat(SECRET, far_future_iat)

    result = validate_session(cookie_value, SECRET, TTL_SEC, clock_skew_sec=300)

    assert result is None, "iat далеко в будущем должен вернуть None"


def test_validate_session_wrong_secret() -> None:
    """Другой secret при валидации → None (HMAC не совпадёт)."""
    cookie_value, _ = create_session(SECRET)

    result = validate_session(cookie_value, "wrong-secret-xyz", TTL_SEC, CLOCK_SKEW)

    assert result is None, "Неверный secret должен вернуть None"


def test_validate_session_bad_format() -> None:
    """Строка 'garbage' (не cookie) → None."""
    result = validate_session("garbage", SECRET, TTL_SEC, CLOCK_SKEW)

    assert result is None, "'garbage' должен вернуть None"


def test_validate_session_wrong_version() -> None:
    """Cookie с версией 'v2' вместо 'v1' → None."""
    cookie_value, _ = create_session(SECRET)
    # Заменяем 'v1' на 'v2' (COOKIE_VERSION = "v1")
    wrong_version_cookie = "v2" + cookie_value[2:]

    result = validate_session(wrong_version_cookie, SECRET, TTL_SEC, CLOCK_SKEW)

    assert result is None, "Неверная версия cookie должна вернуть None"


def test_session_owner_id_format() -> None:
    """owner_id должен быть в формате 'anon:<sid>'."""
    _, info = create_session(SECRET)

    assert info.owner_id == f"anon:{info.sid}", (
        f"owner_id должен быть 'anon:{info.sid}', получено {info.owner_id!r}"
    )


def test_cookie_name_constant() -> None:
    """COOKIE_NAME должен быть 'geo_sim_sid' (переменная конфигурации)."""
    assert COOKIE_NAME == "geo_sim_sid"


def test_cookie_version_constant() -> None:
    """COOKIE_VERSION должен быть 'v1'."""
    assert COOKIE_VERSION == "v1"


# ─── Тесты: Session TTL boundary ─────────────────────────────────────────────

def test_session_expired_exactly_at_ttl_boundary() -> None:
    """now - iat == ttl_sec → валидный (условие > ttl_sec, не >=)."""
    ttl = 100
    now_fixed = int(time.time())
    # iat так что now - iat == ttl → не превышает ttl → valid
    iat = now_fixed - ttl
    cookie = _make_cookie_with_iat(SECRET, iat)

    with patch("app.core.simulator.session.time") as mock_time:
        mock_time.time.return_value = float(now_fixed)
        result = validate_session(cookie, SECRET, ttl_sec=ttl, clock_skew_sec=CLOCK_SKEW)

    assert result is not None, (
        "Сессия прямо на TTL-границе (now - iat == ttl_sec) должна быть валидной (условие '>')"
    )


def test_session_expired_one_second_past_ttl() -> None:
    """now - iat == ttl_sec + 1 → None (одна секунда за TTL-границей)."""
    ttl = 100
    now_fixed = int(time.time())
    iat = now_fixed - ttl - 1  # now - iat = ttl + 1 > ttl → expired
    cookie = _make_cookie_with_iat(SECRET, iat)

    with patch("app.core.simulator.session.time") as mock_time:
        mock_time.time.return_value = float(now_fixed)
        result = validate_session(cookie, SECRET, ttl_sec=ttl, clock_skew_sec=CLOCK_SKEW)

    assert result is None, (
        "Сессия на ttl_sec + 1 должна быть истёкшей (now - iat > ttl_sec)"
    )


def test_session_clock_skew_not_extends_ttl() -> None:
    """clock_skew НЕ расширяет TTL назад. now - iat == ttl_sec + clock_skew_sec → None."""
    ttl = 100
    clock_skew = 50
    now_fixed = int(time.time())
    # now - iat = ttl + clock_skew > ttl → expired (clock_skew не помогает)
    iat = now_fixed - ttl - clock_skew
    cookie = _make_cookie_with_iat(SECRET, iat)

    with patch("app.core.simulator.session.time") as mock_time:
        mock_time.time.return_value = float(now_fixed)
        result = validate_session(cookie, SECRET, ttl_sec=ttl, clock_skew_sec=clock_skew)

    assert result is None, (
        "clock_skew применяется только к future-iat check, "
        "не расширяет TTL назад (now - iat == ttl + clock_skew > ttl → expired)"
    )


# ─── Тесты: Guardrail fail-fast ───────────────────────────────────────────────

def test_guardrail_fails_fast_in_production() -> None:
    """SIMULATOR_SESSION_SECRET='change-me-in-production' при ENV=production → RuntimeError."""
    from app.config import Settings

    # Создаём экземпляр в безопасном dev-режиме
    s = Settings()
    # Имитируем production окружение
    s.ENV = "production"
    s.SIMULATOR_SESSION_SECRET = "change-me-in-production"

    with pytest.raises(RuntimeError, match="SIMULATOR_SESSION_SECRET"):
        s._guardrail_simulator_session_secret()


def test_guardrail_allows_default_in_dev() -> None:
    """SIMULATOR_SESSION_SECRET может быть дефолтным в ENV=dev (guardrail не активен)."""
    from app.config import Settings

    s = Settings()
    s.ENV = "dev"
    s.SIMULATOR_SESSION_SECRET = "change-me-in-production"

    # Не должен бросать исключение в dev-режиме
    s._guardrail_simulator_session_secret()
