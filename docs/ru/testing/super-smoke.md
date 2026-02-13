# Super Smoke Test (v2)

«Cупер смок» (`tests/integration/test_simulator_super_smoke.py`) — это главная линия обороны против регрессий в симуляторе. Он проверяет всё: от старта HTTP-сервера до визуализации графа и корректности клиринга.

## Что он проверяет?

Тест разбит на 3 независимые части:

1.  **Part 1: Fixtures & Visual Contract**
    *   Запускает сценарий `fixtures` через HTTP.
    *   Проверяет `tx-once` и `clearing-once` (атомарные действия дебага).
    *   **Главное**: проверяет, что ребра в событиях `tx.updated` и `clearing.done` реально существуют в `graph/snapshot`.
    *   Это гарантирует, что Simulator UI не упадет с ошибкой "Node/Edge not found".

2.  **Part 2: Real Logic (Deterministic)**
    *   Работает **без HTTP**, напрямую с движками (`RealClearingEngine`).
    *   Создает детерминированную топологию (A->B->C->A) в изолированной SQLite.
    *   Проводит вложенный платеж (`begin_nested` + `commit=False`) — ловит баги транзакций.
    *   Запускает цикл клиринга и проверяет формат патчей (`node_patch`, `edge_patch`).

3.  **Part 3: Real Mode HTTP Startup**
    *   Создает `real` режим через API.
    *   Проверяет, что `run_status` приходит по SSE (heartbeat).
    *   Гарантирует, что real-mode seeding не сломан.

## Когда запускать?

Запускайте этот тест (он быстрый, ~2-3 сек) перед любым коммитом, который касается:
*   Симулятора (Real Mode, Fixtures Mode).
*   SSE-событий (формат полей, сериализация).
*   Базовых механизмов БД (сессии, транзакции).

```powershell
# Запуск
.\.venv\Scripts\python.exe -m pytest tests/integration/test_simulator_super_smoke.py -vv
```

## Как обновлять?

Если вы меняете логику симулятора или состав полей SSE:
1.  Запустите тест — он упадет и покажет diff.
2.  Если изменение намеренное (например, переименовали поле в API) — обновите валидаторы в `test_simulator_super_smoke.py` (`_validate_run_status`, `_validate_clearing_done` и т.д.).
3.  **Не удаляйте проверки**, если они кажутся "лишними" — они там, чтобы frontend не падал молча.

## Артефакты (Postmortem)

При падении тест сохраняет полный дамп (события, логи, эксепшны) в JSON:
`test-results/super-simulator/test_super_smoke_partX_YYYYMMDD-HHMMSS-uuuuuu.json`

Можно включить сохранение дампа даже при успехе (для отладки CI):
```powershell
$env:GEO_TEST_DUMP_SUPER_SIM="1"; .\.venv\Scripts\python.exe -m pytest ...
```
