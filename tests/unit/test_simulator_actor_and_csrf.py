"""Unit-тесты для SimulatorActor и _check_csrf_origin (app/api/deps.py).

Тестирует:
  - SimulatorActor — конструкция для admin / participant / anon
  - _check_csrf_origin — CSRF Origin check для cookie-auth (anon) акторов:
      * пропуск для non-anon
      * пропуск для GET/HEAD/OPTIONS
      * 403 при отсутствии Origin у anon на POST
      * OK при Origin в allowlist
      * 403 при Origin НЕ в allowlist
      * пустой allowlist → разрешить всё (dev mode)
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.deps import SimulatorActor, _check_csrf_origin
from app.config import settings as app_settings
from app.utils.exceptions import ForbiddenException, GeoException


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def _make_request(method: str = "POST", origin: str | None = None) -> MagicMock:
    """Создаёт mock-объект Request с нужными method и headers."""
    req = MagicMock()
    req.method = method
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    req.headers = headers
    return req


# ─── Тесты: SimulatorActor конструкция ───────────────────────────────────────

def test_simulator_actor_admin() -> None:
    """SimulatorActor для admin: kind='admin', owner_id='admin', is_admin=True."""
    actor = SimulatorActor(kind="admin", owner_id="admin", is_admin=True)

    assert actor.kind == "admin"
    assert actor.owner_id == "admin"
    assert actor.is_admin is True
    assert actor.participant_pid is None


def test_simulator_actor_participant() -> None:
    """SimulatorActor для participant: kind='participant', owner_id='pid:xxx'."""
    actor = SimulatorActor(
        kind="participant",
        owner_id="pid:test-participant-123",
        is_admin=False,
        participant_pid="test-participant-123",
    )

    assert actor.kind == "participant"
    assert actor.owner_id == "pid:test-participant-123"
    assert actor.is_admin is False
    assert actor.participant_pid == "test-participant-123"


def test_simulator_actor_anon() -> None:
    """SimulatorActor для anon: kind='anon', owner_id='anon:<sid>'."""
    actor = SimulatorActor(kind="anon", owner_id="anon:abc123", is_admin=False)

    assert actor.kind == "anon"
    assert actor.owner_id == "anon:abc123"
    assert actor.is_admin is False
    assert actor.participant_pid is None


def test_simulator_actor_admin_with_owner_override() -> None:
    """Admin actor с X-Simulator-Owner override: owner_id='cli:<label>'."""
    actor = SimulatorActor(
        kind="admin",
        owner_id="cli:my-script",
        is_admin=True,
    )

    assert actor.kind == "admin"
    assert actor.owner_id == "cli:my-script"
    assert actor.is_admin is True


# ─── Тесты: _check_csrf_origin ───────────────────────────────────────────────

def test_csrf_check_skips_non_anon() -> None:
    """Для admin-актора CSRF-проверка не применяется (kind != 'anon')."""
    actor = SimulatorActor(kind="admin", owner_id="admin", is_admin=True)
    request = _make_request("POST", origin=None)

    # Должно выполниться без исключений
    _check_csrf_origin(request, actor)


def test_csrf_check_skips_participant() -> None:
    """Для participant-актора CSRF-проверка не применяется."""
    actor = SimulatorActor(kind="participant", owner_id="pid:abc", is_admin=False)
    request = _make_request("POST", origin=None)

    # Должно выполниться без исключений
    _check_csrf_origin(request, actor)


def test_csrf_check_skips_get_method() -> None:
    """GET-запрос от anon-актора освобождён от CSRF-проверки."""
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("GET", origin=None)

    # GET exempt — не должно быть исключений
    _check_csrf_origin(request, actor)


def test_csrf_check_skips_head_method() -> None:
    """HEAD-запрос от anon-актора освобождён от CSRF-проверки."""
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("HEAD", origin=None)

    _check_csrf_origin(request, actor)


def test_csrf_check_skips_options_method() -> None:
    """OPTIONS-запрос от anon-актора освобождён от CSRF-проверки."""
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("OPTIONS", origin=None)

    _check_csrf_origin(request, actor)


def test_csrf_check_requires_origin_for_anon_post(monkeypatch) -> None:
    """POST без Origin header от anon-актора → ForbiddenException (403, E006)."""
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin=None)  # нет Origin

    with pytest.raises(ForbiddenException) as exc_info:
        _check_csrf_origin(request, actor)

    assert exc_info.value.status_code == 403
    assert "Origin" in exc_info.value.message or "origin" in exc_info.value.message.lower()


def test_csrf_check_requires_origin_for_anon_put(monkeypatch) -> None:
    """PUT без Origin header от anon-актора → ForbiddenException (403)."""
    monkeypatch.setattr(app_settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "http://allowed.com")
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("PUT", origin=None)

    with pytest.raises(ForbiddenException) as exc_info:
        _check_csrf_origin(request, actor)

    assert exc_info.value.status_code == 403


def test_csrf_check_requires_origin_for_anon_delete(monkeypatch) -> None:
    """DELETE без Origin header от anon-актора → ForbiddenException (403)."""
    monkeypatch.setattr(app_settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "http://allowed.com")
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("DELETE", origin=None)

    with pytest.raises(ForbiddenException) as exc_info:
        _check_csrf_origin(request, actor)

    assert exc_info.value.status_code == 403


def test_csrf_check_passes_with_valid_origin(monkeypatch) -> None:
    """POST с Origin из allowlist от anon-актора → OK (нет исключений)."""
    monkeypatch.setattr(
        app_settings,
        "SIMULATOR_CSRF_ORIGIN_ALLOWLIST",
        "http://localhost:3000,https://geo-sim.example.com",
    )
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin="http://localhost:3000")

    # Должно выполниться без исключений
    _check_csrf_origin(request, actor)


def test_csrf_check_passes_second_origin_in_allowlist(monkeypatch) -> None:
    """POST с Origin — вторым значением в allowlist → OK."""
    monkeypatch.setattr(
        app_settings,
        "SIMULATOR_CSRF_ORIGIN_ALLOWLIST",
        "http://localhost:3000,https://geo-sim.example.com",
    )
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin="https://geo-sim.example.com")

    _check_csrf_origin(request, actor)


def test_csrf_check_blocks_invalid_origin(monkeypatch) -> None:
    """POST с Origin НЕ из allowlist от anon-актора → ForbiddenException (403, E006)."""
    monkeypatch.setattr(
        app_settings,
        "SIMULATOR_CSRF_ORIGIN_ALLOWLIST",
        "http://localhost:3000",
    )
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin="https://evil-attacker.example.com")

    with pytest.raises(ForbiddenException) as exc_info:
        _check_csrf_origin(request, actor)

    assert exc_info.value.status_code == 403
    assert "evil-attacker.example.com" in exc_info.value.message


def test_csrf_check_allows_all_when_empty_allowlist(monkeypatch) -> None:
    """Пустая SIMULATOR_CSRF_ORIGIN_ALLOWLIST → разрешить всё (dev mode).

    Origin всё равно требуется (проверяется до allowlist),
    но при пустом allowlist любой Origin допускается.
    """
    monkeypatch.setattr(app_settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "")
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    # Передаём какой-то Origin (любой)
    request = _make_request("POST", origin="http://some-random-origin.example.com")

    # Пустой allowlist → allow all → нет исключений
    _check_csrf_origin(request, actor)


def test_csrf_check_allows_all_when_none_allowlist(monkeypatch) -> None:
    """SIMULATOR_CSRF_ORIGIN_ALLOWLIST=None → разрешить всё (dev mode)."""
    monkeypatch.setattr(app_settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", None)
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin="http://any-origin.example.com")

    _check_csrf_origin(request, actor)


def test_csrf_check_blocks_missing_origin_even_empty_allowlist(monkeypatch) -> None:
    """Даже при пустом allowlist — отсутствие Origin → ForbiddenException (403).

    Это следует из логики: Origin проверяется ДО allowlist check.
    """
    monkeypatch.setattr(app_settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "")
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin=None)  # нет Origin

    with pytest.raises(ForbiddenException) as exc_info:
        _check_csrf_origin(request, actor)

    assert exc_info.value.status_code == 403


# ─── Тесты: CSRF E006 формат ─────────────────────────────────────────────────

def test_csrf_error_is_forbidden_exception_with_e006(monkeypatch) -> None:
    """POST с невалидным Origin → ForbiddenException с details[code]='E006' и reason='csrf_origin'."""
    monkeypatch.setattr(app_settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "http://localhost:3000")
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin="https://evil.example.com")

    with pytest.raises(ForbiddenException) as exc_info:
        _check_csrf_origin(request, actor)

    assert exc_info.value.details.get("code") == "E006", (
        f"Ожидался 'E006' в details['code'], получено: {exc_info.value.details}"
    )
    assert exc_info.value.details.get("reason") == "csrf_origin", (
        f"Ожидался 'csrf_origin' в details['reason'], получено: {exc_info.value.details}"
    )


def test_csrf_missing_origin_is_forbidden_exception(monkeypatch) -> None:
    """POST без Origin header от anon → ForbiddenException с E006 и reason='csrf_origin'."""
    monkeypatch.setattr(app_settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "http://localhost:3000")
    actor = SimulatorActor(kind="anon", owner_id="anon:abc", is_admin=False)
    request = _make_request("POST", origin=None)  # нет Origin

    with pytest.raises(ForbiddenException) as exc_info:
        _check_csrf_origin(request, actor)

    assert exc_info.value.details.get("code") == "E006", (
        f"Ожидался 'E006' в details['code'], получено: {exc_info.value.details}"
    )
    assert exc_info.value.details.get("reason") == "csrf_origin", (
        f"Ожидался 'csrf_origin' в details['reason'], получено: {exc_info.value.details}"
    )


# ─── Тесты: X-Simulator-Owner trim ───────────────────────────────────────────

def test_owner_header_trimmed(monkeypatch) -> None:
    """X-Simulator-Owner '  anon:abc123  ' → trim → actor.owner_id == 'cli:anon:abc123'."""
    from app.api.deps import require_simulator_actor

    monkeypatch.setattr(app_settings, "ADMIN_TOKEN", "test-admin-trim-token")

    req = _make_request("GET")
    req.cookies = {}

    actor = asyncio.run(
        require_simulator_actor(
            request=req,
            x_admin_token="test-admin-trim-token",
            x_simulator_owner="  anon:abc123  ",  # пробелы с обеих сторон
            token=None,
        )
    )

    assert actor.owner_id == "cli:anon:abc123", (
        f"Ожидалось 'cli:anon:abc123' после trim пробелов, получено: {actor.owner_id!r}"
    )


def test_owner_header_invalid_after_trim_raises_e009(monkeypatch) -> None:
    """X-Simulator-Owner '  invalid!!  ' → trim → невалидные символы → GeoException E009."""
    from app.api.deps import require_simulator_actor

    monkeypatch.setattr(app_settings, "ADMIN_TOKEN", "test-admin-trim-token")

    req = _make_request("GET")
    req.cookies = {}

    async def _run():
        return await require_simulator_actor(
            request=req,
            x_admin_token="test-admin-trim-token",
            x_simulator_owner="  invalid!!  ",  # невалидный после trim (!! не разрешены)
            token=None,
        )

    with pytest.raises(GeoException) as exc_info:
        asyncio.run(_run())

    assert exc_info.value.details.get("code") == "E009", (
        f"Ожидался 'E009' в details['code'], получено: {exc_info.value.details}"
    )
