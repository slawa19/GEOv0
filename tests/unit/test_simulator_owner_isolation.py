"""Unit-тесты для per-owner runtime isolation.

Тестирует методы _SimulatorRuntimeBase:
  - get_active_run_id(owner_id)
  - set_active_run_id(run_id, owner_id)
  - clear_active_run_id(owner_id, run_id)
  - get_all_active_runs()
  - count_active_runs()
  - list_runs(state=..., owner_id=...)
  - RunRecord поля owner_id, owner_kind, created_by

Стратегия: создаём минимальный mock-класс с нужными атрибутами (_lock,
_active_run_id_by_owner, _runs) и назначаем unbound-методы из
_SimulatorRuntimeBase. Это позволяет тестировать логику без полной
инициализации runtime (которая загружает сценарии, БД и т.д.).
"""
import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.run_lifecycle import RunLifecycle
from app.core.simulator.runtime_impl import _SimulatorRuntimeBase
from app.utils.exceptions import ConflictException


# ─── Minimal Mock Runtime ────────────────────────────────────────────────────

class _MinimalRuntime:
    """Минимальный контейнер для тестирования per-owner методов.

    Имеет только атрибуты, которые используются в методах
    get_active_run_id / set_active_run_id / clear_active_run_id /
    get_all_active_runs / count_active_runs / list_runs.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_run_id_by_owner: dict[str, str] = {}
        self._runs: dict[str, RunRecord] = {}

    # Bind unbound methods from _SimulatorRuntimeBase
    get_active_run_id = _SimulatorRuntimeBase.get_active_run_id
    set_active_run_id = _SimulatorRuntimeBase.set_active_run_id
    clear_active_run_id = _SimulatorRuntimeBase.clear_active_run_id
    get_all_active_runs = _SimulatorRuntimeBase.get_all_active_runs
    count_active_runs = _SimulatorRuntimeBase.count_active_runs
    list_runs = _SimulatorRuntimeBase.list_runs


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def _make_run(run_id: str, owner_id: str = "", state: str = "running") -> RunRecord:
    """Создаёт минимальный RunRecord для тестирования."""
    return RunRecord(
        run_id=run_id,
        scenario_id="test-scenario",
        mode="fixtures",
        state=state,
        owner_id=owner_id,
        owner_kind="admin" if owner_id == "admin" else "anon",
        started_at=datetime.now(tz=timezone.utc),
    )


# ─── Тесты: set / get active run ─────────────────────────────────────────────

def test_set_and_get_active_run_id() -> None:
    """set('run1', owner_id='userA') → get('userA') == 'run1'."""
    rt = _MinimalRuntime()

    rt.set_active_run_id("run1", owner_id="userA")

    assert rt.get_active_run_id("userA") == "run1"


def test_get_active_run_id_different_owner_returns_none() -> None:
    """set для userA → get для userB возвращает None."""
    rt = _MinimalRuntime()

    rt.set_active_run_id("run1", owner_id="userA")

    assert rt.get_active_run_id("userB") is None, (
        "Другой owner не должен видеть чужой active run"
    )


def test_two_owners_two_runs() -> None:
    """Каждый owner получает свой run_id независимо."""
    rt = _MinimalRuntime()

    rt.set_active_run_id("runA", owner_id="userA")
    rt.set_active_run_id("runB", owner_id="userB")

    assert rt.get_active_run_id("userA") == "runA"
    assert rt.get_active_run_id("userB") == "runB"


def test_clear_active_run_id() -> None:
    """set + clear → get возвращает None."""
    rt = _MinimalRuntime()

    rt.set_active_run_id("run1", owner_id="userA")
    rt.clear_active_run_id(owner_id="userA", run_id="run1")

    assert rt.get_active_run_id("userA") is None, (
        "После clear active run_id должен быть None"
    )


def test_clear_active_run_id_mismatched_run() -> None:
    """clear с неверным run_id → run_id остаётся активным (не удаляется)."""
    rt = _MinimalRuntime()

    rt.set_active_run_id("run1", owner_id="userA")
    # Попытка очистить другой run_id
    rt.clear_active_run_id(owner_id="userA", run_id="run999")

    assert rt.get_active_run_id("userA") == "run1", (
        "clear с несовпадающим run_id не должен удалять текущий активный run"
    )


def test_get_active_run_id_empty_owner() -> None:
    """Backward compat: get('') возвращает первый активный run (любой owner)."""
    rt = _MinimalRuntime()

    rt.set_active_run_id("run42", owner_id="someOwner")
    # Пустой owner_id — backward compat: вернуть любой активный
    result = rt.get_active_run_id("")

    assert result == "run42", (
        "get с пустым owner_id должен вернуть первый активный run (backward compat)"
    )


def test_get_active_run_id_empty_owner_no_runs() -> None:
    """Backward compat: get('') при отсутствии runs → None."""
    rt = _MinimalRuntime()

    result = rt.get_active_run_id("")

    assert result is None


def test_get_all_active_runs() -> None:
    """get_all_active_runs возвращает копию всех owner→run маппингов."""
    rt = _MinimalRuntime()

    rt.set_active_run_id("runA", owner_id="userA")
    rt.set_active_run_id("runB", owner_id="userB")

    all_runs = rt.get_all_active_runs()

    assert isinstance(all_runs, dict)
    assert all_runs == {"userA": "runA", "userB": "runB"}
    # Проверяем, что это копия (не тот же объект)
    all_runs["userC"] = "runC"
    assert rt.get_active_run_id("userC") is None, (
        "get_all_active_runs должен возвращать копию, а не ссылку"
    )


def test_count_active_runs() -> None:
    """count_active_runs возвращает корректное количество активных runs."""
    rt = _MinimalRuntime()

    assert rt.count_active_runs() == 0

    rt.set_active_run_id("run1", owner_id="userA")
    assert rt.count_active_runs() == 1

    rt.set_active_run_id("run2", owner_id="userB")
    assert rt.count_active_runs() == 2

    rt.clear_active_run_id(owner_id="userA", run_id="run1")
    assert rt.count_active_runs() == 1


# ─── Тесты: list_runs ────────────────────────────────────────────────────────

def test_list_runs_filter_by_state() -> None:
    """list_runs(state='running') возвращает только running записи."""
    rt = _MinimalRuntime()
    rt._runs["r1"] = _make_run("r1", owner_id="userA", state="running")
    rt._runs["r2"] = _make_run("r2", owner_id="userB", state="stopped")
    rt._runs["r3"] = _make_run("r3", owner_id="userC", state="running")

    result = rt.list_runs(state="running")

    run_ids = {r.run_id for r in result}
    assert run_ids == {"r1", "r3"}, (
        f"Ожидаются только 'running' run-ы, получено: {run_ids}"
    )


def test_list_runs_filter_by_owner_id() -> None:
    """list_runs(owner_id='userA') возвращает только записи для userA."""
    rt = _MinimalRuntime()
    rt._runs["r1"] = _make_run("r1", owner_id="userA")
    rt._runs["r2"] = _make_run("r2", owner_id="userB")
    rt._runs["r3"] = _make_run("r3", owner_id="userA")

    result = rt.list_runs(owner_id="userA")

    run_ids = {r.run_id for r in result}
    assert run_ids == {"r1", "r3"}, (
        f"Ожидаются только run-ы для userA, получено: {run_ids}"
    )


def test_list_runs_no_filter() -> None:
    """list_runs() без фильтров возвращает все записи."""
    rt = _MinimalRuntime()
    rt._runs["r1"] = _make_run("r1", owner_id="userA")
    rt._runs["r2"] = _make_run("r2", owner_id="userB")

    result = rt.list_runs()

    assert len(result) == 2


def test_list_runs_filter_by_state_and_owner() -> None:
    """list_runs(state='running', owner_id='userA') — комбинированный фильтр."""
    rt = _MinimalRuntime()
    rt._runs["r1"] = _make_run("r1", owner_id="userA", state="running")
    rt._runs["r2"] = _make_run("r2", owner_id="userA", state="stopped")
    rt._runs["r3"] = _make_run("r3", owner_id="userB", state="running")

    result = rt.list_runs(state="running", owner_id="userA")

    assert len(result) == 1
    assert result[0].run_id == "r1"


def test_list_runs_empty() -> None:
    """list_runs() на пустом runtime возвращает пустой список."""
    rt = _MinimalRuntime()

    result = rt.list_runs()

    assert result == []


# ─── Тесты: RunRecord owner fields ───────────────────────────────────────────

def test_run_record_owner_fields() -> None:
    """RunRecord может хранить owner_id, owner_kind, created_by."""
    record = RunRecord(
        run_id="run-test-001",
        scenario_id="scenario-xyz",
        mode="fixtures",
        state="running",
        owner_id="anon:abc123",
        owner_kind="anon",
        created_by={
            "actor_kind": "anon",
            "owner_id": "anon:abc123",
            "ip": "127.0.0.1",
        },
    )

    assert record.owner_id == "anon:abc123"
    assert record.owner_kind == "anon"
    assert record.created_by is not None
    assert record.created_by["actor_kind"] == "anon"
    assert record.created_by["ip"] == "127.0.0.1"


def test_run_record_default_owner_fields() -> None:
    """RunRecord без owner_id/owner_kind/created_by использует defaults."""
    record = RunRecord(
        run_id="run-default",
        scenario_id="sc-1",
        mode="fixtures",
        state="stopped",
    )

    assert record.owner_id == ""
    assert record.owner_kind == ""
    assert record.created_by is None


def test_run_record_admin_owner() -> None:
    """RunRecord для admin owner."""
    record = RunRecord(
        run_id="run-admin",
        scenario_id="sc-1",
        mode="fixtures",
        state="running",
        owner_id="admin",
        owner_kind="admin",
    )

    assert record.owner_id == "admin"
    assert record.owner_kind == "admin"


def test_run_record_participant_owner() -> None:
    """RunRecord для participant owner использует формат 'pid:<sub>'."""
    record = RunRecord(
        run_id="run-part",
        scenario_id="sc-1",
        mode="fixtures",
        state="running",
        owner_id="pid:some-participant-id",
        owner_kind="participant",
    )

    assert record.owner_id.startswith("pid:")
    assert record.owner_kind == "participant"


# ─── Вспомогательные функции для RunLifecycle тестов ─────────────────────────

def _make_lifecycle(
    *,
    max_active: int = 10,
    get_active_run_id_for_owner=None,
    existing_runs: dict | None = None,
) -> RunLifecycle:
    """Создаёт минимальный RunLifecycle для тестирования conflict-ов.

    Мокирует все зависимости, которые не нужны для exception-проверок.
    Exception происходит внутри lock ДО artifacts/heartbeat вызовов.
    """
    runs: dict = dict(existing_runs or {})

    async def _noop_heartbeat(run_id: str) -> None:
        pass  # pragma: no cover

    return RunLifecycle(
        lock=threading.RLock(),
        runs=runs,
        set_active_run_id=lambda run_id, owner_id: None,
        utc_now=lambda: datetime.now(tz=timezone.utc),
        new_run_id=lambda: str(uuid.uuid4()),
        get_scenario_raw=lambda sid: {"equivalents": [], "edges": [], "scenario_id": sid},
        edges_by_equivalent=lambda s: {},
        artifacts=MagicMock(),
        sse=MagicMock(),
        heartbeat_loop=_noop_heartbeat,
        publish_run_status=lambda run_id: None,
        run_to_status=MagicMock(),
        get_run_status_payload_json=MagicMock(),
        real_max_in_flight_default=1,
        get_max_active_runs=lambda: max_active,
        get_max_run_records=lambda: 100,
        logger=MagicMock(),
        get_active_run_id_for_owner=get_active_run_id_for_owner,
    )


# ─── Тесты: per-owner 409 conflict_kind ──────────────────────────────────────

def test_create_run_owner_active_exists_conflict() -> None:
    """Создать run для owner, попытаться создать второй → owner_active_exists."""
    existing_run_id = "run-owner-existing-abc"
    owner_id = "anon:abc123"

    lc = _make_lifecycle(
        get_active_run_id_for_owner=lambda oid: existing_run_id if oid == owner_id else None,
    )

    async def _run() -> None:
        await lc.create_run(
            scenario_id="test-scenario",
            mode="fixtures",
            intensity_percent=100,
            owner_id=owner_id,
        )

    with pytest.raises(ConflictException) as exc_info:
        asyncio.run(_run())

    assert exc_info.value.details["conflict_kind"] == "owner_active_exists", (
        f"Ожидалось 'owner_active_exists', получено: {exc_info.value.details}"
    )
    assert exc_info.value.details["active_run_id"] == existing_run_id


def test_create_run_global_limit_conflict_kind() -> None:
    """Заполнить глобальный лимит, создать ещё → global_active_limit."""
    # Два running run, лимит = 2 → достигнут
    existing_runs = {
        "run1": _make_run("run1", owner_id="userA", state="running"),
        "run2": _make_run("run2", owner_id="userB", state="running"),
    }
    lc = _make_lifecycle(max_active=2, existing_runs=existing_runs)

    async def _run() -> None:
        await lc.create_run(
            scenario_id="test-scenario",
            mode="fixtures",
            intensity_percent=100,
            owner_id="anon:new-owner",
        )

    with pytest.raises(ConflictException) as exc_info:
        asyncio.run(_run())

    assert exc_info.value.details["conflict_kind"] == "global_active_limit", (
        f"Ожидалось 'global_active_limit', получено: {exc_info.value.details}"
    )


def test_create_run_owner_limit_checked_before_global() -> None:
    """Owner уже имеет run + глобальный лимит достигнут → owner_active_exists (не global)."""
    owner_id = "anon:owner-with-existing-run"
    existing_run_id = "run-owner-already-has"

    # Глобальный лимит = 1, уже есть 1 running run → глобальный лимит тоже достигнут
    existing_runs = {
        "run-other": _make_run("run-other", owner_id="userX", state="running"),
    }
    lc = _make_lifecycle(
        max_active=1,
        existing_runs=existing_runs,
        # Owner нашего запроса уже имеет активный run
        get_active_run_id_for_owner=lambda oid: existing_run_id if oid == owner_id else None,
    )

    async def _run() -> None:
        await lc.create_run(
            scenario_id="test-scenario",
            mode="fixtures",
            intensity_percent=100,
            owner_id=owner_id,
        )

    with pytest.raises(ConflictException) as exc_info:
        asyncio.run(_run())

    # Per-owner check выполняется ПЕРВЫМ → должен быть owner_active_exists
    assert exc_info.value.details["conflict_kind"] == "owner_active_exists", (
        f"Ожидалось 'owner_active_exists' (сначала per-owner check), "
        f"получено: {exc_info.value.details.get('conflict_kind')}"
    )


# ─── Тесты: AuthZ deny-by-default для пустого owner ──────────────────────────

def test_check_run_access_empty_owner_denies_non_admin() -> None:
    """run с owner_id='', actor non-admin → 403 (deny-by-default §7)."""
    from app.api.v1.simulator import _check_run_access
    from app.api.deps import SimulatorActor
    from app.utils.exceptions import ForbiddenException

    run = _make_run("run-no-owner", owner_id="")
    actor = SimulatorActor(kind="anon", owner_id="anon:someone", is_admin=False)

    with pytest.raises(ForbiddenException) as exc_info:
        _check_run_access(run, actor, "run-no-owner")

    assert exc_info.value.status_code == 403


def test_check_run_access_empty_owner_allows_admin() -> None:
    """run с owner_id='', actor admin → OK (deny-by-default не применяется к admin)."""
    from app.api.v1.simulator import _check_run_access
    from app.api.deps import SimulatorActor

    run = _make_run("run-no-owner", owner_id="")
    actor = SimulatorActor(kind="admin", owner_id="admin", is_admin=True)

    # Не должно выбрасывать исключение
    _check_run_access(run, actor, "run-no-owner")


# ─── Тесты: restart восстанавливает active_run_id ────────────────────────────

def test_restart_restores_active_run_id() -> None:
    """After stop + restart, active_run_id mapping is restored."""
    active_map: dict[str, str] = {}
    owner_id = "anon:restart-test-owner"
    run_id = "run-restart-restore-001"

    def _set_active(rid: str, oid: str) -> None:
        if oid:
            active_map[oid] = rid

    lc = _make_lifecycle(
        get_active_run_id_for_owner=lambda oid: active_map.get(oid),
    )
    # Подменяем set_active_run_id реальным хранилищем
    lc._set_active_run_id = _set_active

    # Добавляем run в stopped состоянии (маппинг пуст — как после stop())
    run = _make_run(run_id, owner_id=owner_id, state="stopped")
    lc._runs[run_id] = run
    assert active_map.get(owner_id) is None, "Маппинг должен быть пустым перед restart"

    async def _do_restart() -> None:
        await lc.restart(run_id)

    with patch("app.core.simulator.run_lifecycle.simulator_storage") as mock_storage:
        mock_storage.upsert_run = AsyncMock(return_value=None)
        asyncio.run(_do_restart())

    # После restart маппинг должен быть восстановлен
    assert active_map.get(owner_id) == run_id, (
        f"Ожидается active_run_id={run_id!r} после restart, "
        f"получено: {active_map.get(owner_id)!r}"
    )


def test_restart_conflict_with_another_active_run() -> None:
    """Restart fails if owner already has a different active run."""
    run1_id = "run-conflict-restart-r1"
    run2_id = "run-conflict-restart-r2"
    owner_id = "anon:conflict-restart-owner"

    # Маппинг указывает на run2 (другой активный run того же owner)
    active_map: dict[str, str] = {owner_id: run2_id}

    lc = _make_lifecycle(
        get_active_run_id_for_owner=lambda oid: active_map.get(oid),
    )

    # run1 в stopped состоянии, пытаемся его перезапустить
    run1 = _make_run(run1_id, owner_id=owner_id, state="stopped")
    lc._runs[run1_id] = run1

    async def _do_restart() -> None:
        await lc.restart(run1_id)

    with pytest.raises(ConflictException) as exc_info:
        asyncio.run(_do_restart())

    assert exc_info.value.details["conflict_kind"] == "owner_active_exists", (
        f"Ожидалось 'owner_active_exists', получено: {exc_info.value.details}"
    )
    assert exc_info.value.details["active_run_id"] == run2_id


# ─── TODO: Тесты _get_run_checked helper ─────────────────────────────────────
#
# _get_run_checked зависит от глобального `runtime` объекта (см. _get_runtime()),
# который требует полной инициализации _SimulatorRuntime (сценарии, БД и т.д.).
# Unit-тестирование без патчинга глобального runtime нецелесообразно.
#
# _check_run_access уже покрыт тестами:
#   - test_check_run_access_empty_owner_denies_non_admin
#   - test_check_run_access_empty_owner_allows_admin
#
# def test_get_run_checked_returns_run_for_owner():
#     """_get_run_checked returns run for matching owner."""
#
# def test_get_run_checked_raises_403_for_wrong_owner():
#     """_get_run_checked raises 403 for non-owner non-admin."""
#
# def test_get_run_checked_raises_409_for_stopped_run():
#     """_get_run_checked raises 409 if run is stopped."""
