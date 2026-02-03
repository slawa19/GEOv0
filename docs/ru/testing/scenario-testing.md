# Тестирование сценариев (CLI)

Документ живой: пополняется по мере исправления падений.

> **См. также:** [Быстрый старт и отладка](quick-start-and-debugging.md) — общее руководство по запуску проекта, unit-тестам и отладке типичных проблем.

## Быстрый старт

### 1) Поднять Postgres (dev)

- VS Code Task: `Postgres: ensure running (docker compose)`

### 2) Убедиться что тестовая БД существует

- VS Code Task: `Postgres: ensure geov0_test exists (docker)`

### 3) Запустить сценарный набор тестов

- VS Code Task: `Pytest: TS-23 (Postgres)`

### 4) Запуск через CLI (полезно для полного traceback)

```powershell
$env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@localhost:5432/geov0_test'
$env:GEO_TEST_ALLOW_DB_RESET='1'
D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe -m pytest -q tests/integration/test_scenarios.py -vv
```

## Smoke realistic-v2 (Real Mode) — быстрый прогон с анализом

Цель: быстро проверить, что Real Mode realistic-v2 даёт «реалистичные» суммы (десятки/сотни UAH), пишет артефакты и не ограничен legacy-капом `3.00`.

Рекомендуемо (VS Code Tasks):
- `Full Stack: restart (cap=500)`
- `Simulator: run realistic-v2 smoke + analyze`

Что считается успехом:
- в выводе smoke есть `payments.over_3 > 0` и `payments.over_500 == 0`;
- список `artifacts=` содержит `events.ndjson`;
- в `events.ndjson` есть хотя бы одно доменное событие `type=tx.updated` (а не только `run_status`).

Примечание:
- Если в окне прогона в БД видны COMMITTED платежи, но в `events.ndjson` нет `tx.updated`, это баг в цепочке «runner → SSE/артефакты → UI» и визуализация закономерно будет пустой.

Где смотреть артефакты:
- `.local-run/analysis/<run_id>/` (скачанные файлы: `events.ndjson`, `summary.json`, ...)
- `.local-run/simulator/runs/<run_id>/artifacts/` (runtime-артефакты)

## Что считается «сценарным тестом»

- Интеграционные тесты, которые требуют поднятой БД и прогоняют end-to-end путь через сервисы/репозитории.

## Диагностика типовых проблем

### Docker / Postgres не стартует

- Симптом: задачи `Postgres:*` падают.
- Проверки:
  - Docker доступен в системе (CLI `docker` должен быть в PATH).
  - WSL2/Engine запущен (если используется Docker Desktop + WSL).

### Нет подключения к БД

- Симптом: ошибки подключения/таймауты в pytest.
- Проверки:
  - Postgres контейнер реально поднят.
  - Порт/DSN соответствуют конфигу тестов.

### asyncpg (Windows): `Event loop is closed` / `another operation is in progress`

- Симптомы:
  - `RuntimeError: Event loop is closed`
  - `asyncpg.exceptions.InterfaceError: cannot perform operation: another operation is in progress`
- Причина:
  - Пул соединений asyncpg может переиспользовать соединение, привязанное к уже закрытому event loop (pytest-asyncio по умолчанию создаёт loop на тест).
- Решение (уже применено в фикстурах тестов):
  - Использовать `NullPool` для async engine в [tests/conftest.py](tests/conftest.py)

## Артефакты прогона

- Логи задач VS Code (Task Output)
- Pytest traceback

## Чек-лист перед запуском

- [ ] Postgres поднят
- [ ] `geov0_test` создана
- [ ] Запуск `Pytest: TS-23 (Postgres)` проходит локально
