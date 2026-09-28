# 021, стадия 2 (`T2103`) — evidence, 2026-09-28

Ветка `claude/021-s2` от `3f23a39` (слияние PR #68, стадия 1). Все прогоны — канонический раннер `.\scripts\verify_local.ps1 -TaskSlug p021s2 -BackendOnly -Python <.venv>` (база `geov0_test_p021s2` выводится раннером); red-прогон с маркерами снят через `PYTEST_ADDOPTS=--runxfail`.

## Red-first

Коммит `1052ee93` (код `3f23a39`): R-021-6 (`tests/integration/test_p021_interact_and_inject_trust_lines_are_audited_postgres.py`) и R-021-1 (`tests/unit/test_p021_simulator_writes_trust_lines_only_through_the_service.py`) с `xfail(strict=True, raises=TargetMismatch)`. С маркерами на `3f23a39`: `6 passed, 8 xfailed`. С `--runxfail` — каждая цель падает на своём `TargetMismatch`:

- Interact: `audit rows [], checkpoints per action [0, 0, 0]`;
- Interact create выше долга: `audit rows 0, checkpoints 0` (отказ ниже долга — зелёный контроль: 409 `USED_EXCEEDS_NEW_LIMIT`, линии нет, 0 точек);
- откат обработчиком, три действия: `checkpoint failure point reached: False (0 computations); raised None; response 200; line after (70, 'active') (expected None)` / `(70, 'active') (expected (100, 'active'))` / `(100, 'closed') (expected (100, 'active'))`;
- инжект: `audit rows [], checkpoints 0`;
- откат владельцем инжекта: `checkpoint failure point reached: False (0 computations); raised None; lines after [(A,B,E1,10,active,…), (A,B,E2,20,active,…)]; 0 audit rows; fired [0]`;
- R-021-1: пять мест — `inject_executor.py:848`, `:703` `TrustLine(...)`; `api/v1/simulator.py:1151` `TrustLine(...)`, `:1393` `tl.limit = new_limit_dec`, `:1575` `tl.status = 'closed'`.

Коммит `de499dec` добавил второй узел отката инжекта (отказ точки «до» второй линии, внутри обработчика эффекта); тот же файл на коде `3f23a39` (`git checkout 3f23a39f -- <три файла app>`, прогон, возврат) — 8 из 8 целей красные на `TargetMismatch`, новый узел — `failure point reached: False …; fired [0]`.

## Места записи `trust_lines` из симулятора (девять мест Problem п. 1; якоря — HEAD стадии)

| Место (`4761c81`) | Сейчас | Стадия |
|---|---|---|
| `real_scenario_seeder.py:277` `TrustLine(...)` | `import_initial_trustlines` (`real_scenario_seeder.py:279`) | 1 — перенесено |
| `trust_drift_engine.py:324` рост | `execute_update(require_signature=False)` (`trust_drift_engine.py:61`, пакет `:277`) | 1 — перенесено |
| `trust_drift_engine.py:531` затухание | то же (пакет `:474`) | 1 — перенесено |
| `inject_executor.py:703` начальные линии `add_participant` | `write_trustline` → `execute_create(require_signature=False, flush=False)` (`inject_executor.py:773` → `:480`), пакет события `:472`, `finish()` `:1073` | **2 — перенесено** |
| `inject_executor.py:848` `create_trustline` | то же (`:917` → `:480`) | **2 — перенесено** |
| `inject_executor.py:939` заморозка | `frozen_tl.status = "frozen"` (`inject_executor.py:1009`) | **поимённое исключение**, гард R-021-1 с контрпроверкой |
| `api/v1/simulator.py:1140` create | `execute_create(require_signature=False)` (`api/v1/simulator.py:1183`, пакет `:1180`) | **2 — перенесено** |
| `api/v1/simulator.py:1382` update | `execute_update(require_signature=False)` (`:1433`, пакет `:1431`) | **2 — перенесено** |
| `api/v1/simulator.py:1564` close | `execute_close(require_signature=False)` (`:1627`, пакет `:1625`) | **2 — перенесено** |

`git grep -nE "TrustLine\(" -- app/core/simulator app/api/v1/simulator.py` — ноль конструкторов; `update(TrustLine)` в `app/` — ноль. Вызовы `execute_*` с литералом `False` — ровно пять модулей из перечня (сидер — импорт, дрейф, инжект, Interact); публичные обёртки — `True` (`service.py:302`, `:340`, `:358`).

## Мутации (каждая применена скриптом к коду стадии, прогнан R-021-6, возвращена `git checkout -- app`)

| Мутация | Результат |
|---|---|
| Убран откат владельца инжекта (`real_runner_impl.py`, обработчик `except Exception` после стейджинга) | 2 failed (оба узла отката инжекта): поздний коммит отклонён детектором незавершённого пакета |
| То же + детектор выключен | **8 passed** — данные уже сняты откатом savepoint'а конверта `Book.operation` (`book.py:990-993`), внутри которого идёт стейджинг |
| То же + откат savepoint'а `Book` убран | 2 failed: коммит отклонён отложенной проверкой БД «операция `INJECT` ещё `OPEN`» |
| `InjectTrustLineWriteFailed` убран из кортежей проброса обработчиков эффектов | 1 failed — узел `second_line_before_checkpoint`: отказ проглочен как «пропущено», первая линия закоммичена |
| Убран откат в трёх обработчиках Interact (с детектором / без) | 3 failed / 3 failed — узлы create, update, close |
| Контрольная точка «до» на каждый вызов | 1 failed — инжект: 5 вычислений на 3 линии в 2 эквивалентах (ожидалось 4); Interact не краснеет (одна операция на транзакцию — точек и так 2) |
| Убрана проверка существующего долга Interact create | 1 failed — `test_interact_create_keeps_its_existing_debt_check` (линия ниже долга создана) |
| Только детектор выключен | 8 passed — откаты держат сами, детектор вторичен |
