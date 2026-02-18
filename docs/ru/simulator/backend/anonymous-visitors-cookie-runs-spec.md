# Anonymous visitors (cookie) → per-owner simulator runs + admin control (spec)

**Status:** draft (2026-02-18)

## 0) Контекст и проблема

Сейчас Simulator Real Mode использует защищённые эндпоинты `/api/v1/simulator/*`, которые требуют:
- `Authorization: Bearer <participant_jwt>` **или**
- `X-Admin-Token: <token>`.

Runtime симулятора — **in-process** (in-memory):
- есть глобальная переменная `active_run_id` (одна на процесс)
- есть глобальный лимит `SIMULATOR_MAX_ACTIVE_RUNS` (по умолчанию 1)

Это делает невозможным целевой прод-юзкейс:
- “анонимные посетители” (без логина) запускают симулятор и **видят только свой run**
- админ видит **все** run’ы и может подключиться/остановить
- автотесты/CLI тоже стартуют симуляторы и должны оставаться совместимыми

## 1) Цели / не-цели

### 1.1 Цели
1) Добавить **анонимную идентичность** посетителя на основе cookie (без логина).
2) Сделать “активный run” и лимиты — **пер-владелец (per-owner)**, а не глобальными.
3) Изоляция: обычный анонимный посетитель может читать/управлять **только своим run’ом**.
4) Админ:
   - видит **все процессы** (runs)
   - может подключиться к SSE и читать состояние любого run
   - может остановить любой run
5) CLI/автотесты:
   - могут стартовать run’ы через HTTP (как сейчас)
   - имеют способ избежать конфликтов 409 при параллельном выполнении (owner override)
   - не ломают существующие тесты в “последовательном” режиме

### 1.2 Не-цели
- Не проектируем полноценную систему пользователей/ролей для посетителей (кроме админа).
- Не делаем распределённый runtime (Redis/Streams) в рамках этой доработки.
- Не добавляем новый UI экран/страницу для админ-панели (это отдельная задача). В рамках этой спеки фиксируем только backend API/контракты, которые UI может использовать.

## 2) Термины

- **Actor** — субъект, от имени которого делается запрос к simulator API.
- **Owner** — идентификатор владения run’ом (строка), вычисляется из actor.
- **Anon session** — анонимная сессия посетителя, представленная cookie.

## 3) Архитектурное решение: actor → owner_id

Каждый запрос к `/api/v1/simulator/*` вычисляет `actor` и **всегда** получает `owner_id` (строка), по правилам:

**Порядок приоритета (важно):**
1) Если присутствует валидный `X-Admin-Token` → `actor.kind = admin`, `actor.is_admin = true`, `owner_id = "admin"`.
2) Если `X-Admin-Token` валиден **и** присутствует `X-Simulator-Owner` → `owner_id = "cli:<normalized>"` (admin-only override; см. раздел 9).
3) Иначе если есть валидный `Authorization: Bearer <jwt>` → `actor.kind = participant`, `owner_id = "pid:<sub>"`.
4) Иначе если есть валидная анонимная cookie-сессия → `actor.kind = anon`, `owner_id = "anon:<sid>"`.
5) Иначе → 401 (для UI cookie-mode это исправляется вызовом `POST /session/ensure`).

**Важно:** для SSE (EventSource) кастомные заголовки в браузере неудобны/ограничены. Cookie-actor даёт самый надёжный способ анонимной авторизации для SSE.

## 4) Cookie сессия (anon)

### 4.1 Cookie формат

Cookie должна быть:
- `HttpOnly` (чтобы JS не мог прочитать)
- `SameSite=Lax` (default)
- `Secure=true` в prod (в dev может быть `false`, если используется http)
- `Path=/`.

**Имя:** `geo_sim_sid` (v1)

**Значение (stateless, подписанное, компактный формат):**

`v1.<sid_b64url>.<iat_dec>.<sig_b64url>`

где:
- `sid_b64url` — 16 байт random, base64url без `=` (128-bit entropy)
- `iat_dec` — issued-at epoch seconds (десятичная строка)
- `sig_b64url` — `HMAC-SHA256(secret, "v1|<sid_b64url>|<iat_dec>")`, base64url без `=`

**Валидация:**
- версия должна быть `v1`
- `iat` не в будущем более чем на `SIMULATOR_SESSION_CLOCK_SKEW_SEC` (default 300)
- `now - iat <= SIMULATOR_SESSION_TTL_SEC`
- подпись совпадает (constant-time compare)

**Секрет:** `SIMULATOR_SESSION_SECRET`.
- В `dev/test` допускается дефолт (для удобства),
- В остальных окружениях секрет **обязателен** (guardrail на старте процесса; см. `app/config.py`).

### 4.2 Retention

- Cookie TTL: `SIMULATOR_SESSION_TTL_SEC` (default 7d)
- Сервер принимает cookie, если `now - iat <= TTL`.

### 4.3 Revocation (важное ограничение)

Так как cookie **stateless**, точечной “отзывной” системы в MVP нет.

Поддерживаемые варианты:
- истечение TTL
- rotation `SIMULATOR_SESSION_SECRET` (инвалидирует **все** текущие cookie)

Если потребуется точечный revoke (например, abuse), это отдельная задача (deny-list в DB/Redis).

### 4.4 Endpoint bootstrap

Добавить endpoint:

`POST /api/v1/simulator/session/ensure`

Ответ:
```json
{ "actor_kind": "anon", "owner_id": "anon:<sid>" }
```

Поведение:
- Если cookie отсутствует/просрочена/невалидна → установить новую и вернуть owner_id
- Если валидна → вернуть текущий owner_id

Ошибки:
- если cookie-mode включён, но `SIMULATOR_SESSION_SECRET` не настроен (что не должно происходить вне dev/test из-за guardrail) → 500:
   ```json
   { "error": { "code": "E010", "message": "Internal server error", "details": { "reason": "simulator_session_secret_missing" } } }
   ```

**Примечание по dev cross-origin:** backend уже включает `allow_credentials=True` и allow-origin regex для localhost. UI запросы должны посылать cookie с `credentials: 'include'`.

### 4.5 CSRF (cookie-mode)

Cookie-mode добавляет CSRF-риски для **state-changing** запросов (POST/PATCH/PUT/DELETE).

Минимальная политика для MVP (совместима с текущим dev-стеком `localhost:*`):
- cookie `SameSite=Lax`
- для state-changing запросов, которые авторизуются **через cookie** (т.е. нет `Authorization` и нет `X-Admin-Token`), backend требует `Origin` header
   - `Origin` должен совпадать с allowlist (например, `http://localhost:5176`, `http://127.0.0.1:5176` в dev)
   - если `Origin` отсутствует или не разрешён → 403:
      ```json
      { "error": { "code": "E006", "message": "Insufficient permissions", "details": { "reason": "csrf_origin" } } }
      ```

Расширение (не требуется для первой итерации, но совместимо): double-submit token:
- cookie `geo_sim_csrf` (НЕ HttpOnly)
- header `X-CSRF-Token: <cookie_value>`

## 5) Модель данных: owner поля в run

### 5.1 In-memory

Расширить `RunRecord`:
- `owner_id: str` (обязательное)
- `owner_kind: str` (`anon|participant|admin|cli`), опционально
- `created_by: dict` (best-effort диагностика):
  - `created_by_kind`, `created_by_pid`, `created_by_owner_id`, `created_by_admin: bool`

### 5.2 DB (опционально, если `SIMULATOR_DB_ENABLED=1`)

Расширить таблицу `simulator_runs` (см. `app/db/models/simulator_storage.py`):
- `owner_id TEXT` (для новых записей **обязательное**)
- `owner_kind TEXT NULL`

Индексы:
- `INDEX (owner_id, state, created_at)`

Миграция должна быть совместимой с SQLite dev.

**Миграция данных (важно):**
- для существующих строк заполнить `owner_id = 'legacy:unknown'`, `owner_kind = NULL`
- после этого можно сделать `owner_id` NOT NULL (SQLite: через copy-table миграцию alembic)

## 6) Runtime: active run per owner

### 6.1 Новые структуры

В runtime добавить:
- `active_run_id_by_owner: dict[str, str]` (in-memory)

### 6.2 Правило “active run”

“Активный” — state ∈ {`running`, `paused`, `stopping`}.

Для owner:
- `GET active_run(owner_id)` возвращает run_id только если run ещё не terminal.

### 6.3 Лимиты

Нужно два уровня лимитов и **два различимых вида конфликта**:

1) **Per-owner**: `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER` (default 1)
   - предотвращает конфликт “две вкладки одного посетителя”
2) **Global**: `SIMULATOR_MAX_ACTIVE_RUNS` (как сейчас, default 1)
   - защита ресурсов процесса

При `create_run` должны быть enforced оба:

1) Если owner уже имеет активный run → 409 `E008`:
```json
{
   "error": {
      "code": "E008",
      "message": "State conflict",
      "details": {
         "conflict_kind": "owner_active_exists",
         "owner_id": "anon:...",
         "active_run_id": "run_..."
      }
   }
}
```

2) Если глобальный лимит достигнут → 409 `E008`:
```json
{
   "error": {
      "code": "E008",
      "message": "State conflict",
      "details": {
         "conflict_kind": "global_active_limit",
         "max_active_runs": 1,
         "active_runs": 1
      }
   }
}
```

**UI правило:**
- на `owner_active_exists` UI должен attach’иться к `active_run_id` (или через `/runs/active`)
- на `global_active_limit` UI должен показать явную ошибку “сервер занят” (и не attach’иться к чужому run)

### 6.4 Поведение stop

При переходе run в terminal (`stopped|error`):
- если `active_run_id_by_owner[owner_id] == run_id` → удалить ключ
- (опционально) если legacy `_active_run_id == run_id` → очистить (для обратной совместимости)

## 7) Авторизация на run endpoints

### 7.1 Правило доступа

Для эндпоинтов вида:
- `/runs/{run_id}`
- `/runs/{run_id}/pause|resume|stop|restart|intensity`
- `/runs/{run_id}/graph/snapshot`, `/runs/{run_id}/events`, `/runs/{run_id}/metrics`, `/runs/{run_id}/bottlenecks`, `/runs/{run_id}/artifacts/*`

Доступ разрешён если:
- actor.is_admin == true
**или**
- `run.owner_id == actor.owner_id`

Иначе → 403.

### 7.2 Actor dependency

Добавить новый dependency в `app/api/deps.py`, условно:
- `require_simulator_actor()`

Он должен:
- принимать admin token (как сейчас)
- принимать participant jwt (как сейчас)
- принимать cookie session (новое)
- возвращать структурированный объект `SimulatorActor` (не `Participant|None`), например:
  ```py
  class SimulatorActor(BaseModel):
      kind: Literal['admin','participant','anon']
      owner_id: str
      is_admin: bool
      participant_pid: str|None
  ```

`require_participant_or_admin` остаётся для остальных API (не симуляторных).

## 8) API изменения (симулятор)

### 8.1 Session
- `POST /api/v1/simulator/session/ensure` (новый)

Примечание по rate-limit: текущая реализация добавляет `deps.rate_limit` глобально на HTTP-роутеры.
Для UX важно либо:
- исключить `session/ensure` из rate-limit, либо
- поднять лимит/отдельный bucket, чтобы страница не “умирала” от 429 при перезагрузках.

### 8.2 Runs

Существующие эндпоинты сохраняются, но семантика меняется на per-owner:

1) `POST /api/v1/simulator/runs`
   - создаёт run для `actor.owner_id`

2) `GET /api/v1/simulator/runs/active`
   - возвращает active run **только для actor.owner_id**
   - **контракт:** всегда 200
     - если активного нет → `{ "run_id": null }`
     - если есть → `{ "run_id": "..." }`

3) Legacy active endpoints (без `{run_id}`):
   - `/api/v1/simulator/graph/snapshot`
   - `/api/v1/simulator/graph/ego`
   - `/api/v1/simulator/events`

   Новая семантика: работают с `active_run(owner_id=actor.owner_id)`, а не с глобальным `runtime._active_run_id`.

### 8.3 Admin control plane

Добавить новые админ-эндпоинты (строго `deps.require_admin`):

1) `GET /api/v1/simulator/admin/runs`
   - query: `state` (optional), `owner_id` (optional), `limit`, `offset`
   - ответ: список run’ов с полями owner + state + timestamps + scenario

2) `POST /api/v1/simulator/admin/runs/stop-all` (dev/ops helper)
   - query: `state=running|paused|stopping|*` (default `*`)
   - body: `{ reason?: string }`
   - ответ: `{ stopped: number }`

Примечание: “active run любого owner” для админа достигается через `GET /api/v1/simulator/runs/active` + `X-Simulator-Owner` (см. раздел 9), без отдельного `/admin/runs/active`.

Примечание: админ и так может останавливать run через существующий `POST /runs/{run_id}/stop`, но stop-all нужен как быстрый “уборщик” после тестов/демо.

## 9) CLI / автотесты (HTTP)

### 9.1 Требование

Автотесты и CLI запускают симулятор через HTTP с `X-Admin-Token`.
После перехода на per-owner модель, если все тесты будут использовать одного owner (`admin`), то параллельные запуски будут конфликтовать по `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER`.

### 9.2 Решение: owner override (только для admin)

Добавить поддержку заголовка:
- `X-Simulator-Owner: <string>`

Правило:
- если `X-Admin-Token` валиден и заголовок присутствует → использовать `owner_id = "cli:<normalized>"`
- если admin token нет → игнорировать заголовок

`<normalized>`:
- trim
- длина 1..64
- допустимые символы: `[A-Za-z0-9._:-]`
- иначе 422 `E009` (validation error)

Где применяется:
- `POST /simulator/runs`
- `GET /simulator/runs/active`
- legacy active endpoints (если они остаются)

**Рекомендация для тестов:** формировать `X-Simulator-Owner` из `pytest` nodeid (хеш) или имени теста, чтобы owner был уникальным.

### 9.3 Совместимость

Если `X-Simulator-Owner` не задан, поведение для админ-токена остаётся “как раньше” (все запросы от одного owner), что позволяет не ломать последовательные тесты сразу.

## 10) UI (Simulator UI v2) — требования к интеграции

### 10.1 Cookie auth режим

UI должен уметь работать без `accessToken`, в режиме cookie:

Стартовый bootstrap:
1) если `mode=real` и `accessToken` пустой → вызвать `POST /api/v1/simulator/session/ensure`
2) далее обычные запросы к simulator API

### 10.2 Важное: fetch credentials

Для localhost dev UI (5176) → backend (18000) это cross-origin.
Чтобы cookie отправлялись:
- `fetch(..., { credentials: 'include' })` на всех HTTP запросах simulator API
- SSE (в проекте SSE реализован через `fetch`-стриминг) тоже должен быть `credentials: 'include'`.

В token-mode это не требуется, но добавление `credentials: 'include'` безопасно и упрощает поддержку.

### 10.3 Admin UX

Функционально админ уже может подключаться/останавливать, имея run_id.
Если потребуется UI-интерфейс для “видеть все процессы”, он должен использовать:
- `GET /api/v1/simulator/admin/runs`

Но конкретная UX реализация админ-панели не входит в эту задачу.

### 10.4 Где именно это видно в UI (минимальный UX без новых страниц)

Цель: сделать админские функции доступными **внутри существующего Simulator UI** (тот же экран), не добавляя отдельную страницу.

**Гейт:** показывать админские элементы только если UI работает в admin-режиме:
- `accessToken` задан и он не JWT-похожий (то есть уйдёт как `X-Admin-Token`),
- либо (если позже появится) backend выдаёт `actor_kind=admin` из `/simulator/session/ensure`.

**Расположение (предпочтительно):** в верхней панели управления (компонент TopBar), рядом с блоком Run controls.

Минимальный набор контролов (в одном компактном блоке “Admin”):
1) **Runs list**
   - Кнопка/селект “Runs…” → подгружает `/api/v1/simulator/admin/runs?state=running|paused|stopping`.
   - В списке показывать: `run_id` (укороченно), `state`, `scenario_id`, `owner_id`.
2) **Attach**
   - Действие “Attach” устанавливает `real.runId` в выбранный `run_id` и запускает обычный refresh+SSE (как для обычного пользователя).
3) **Stop selected**
   - Вызывает `POST /api/v1/simulator/runs/{run_id}/stop?source=ui&reason=admin_stop`.
4) **Stop all** (опционально, но полезно для dev/оператора)
   - Вызывает `POST /api/v1/simulator/admin/runs/stop-all`.

**Почему TopBar:**
- это место, где уже есть Start/Pause/Stop и отображение статуса run/SSE;
- админские действия относятся к control-plane и не должны “прятаться” в графе/сцене.

**Важно:** этот UX не требует изменения основной логики рендера/анимации — админ просто выбирает run и подключается как наблюдатель.

## 11) Безопасность

- Cookie auth требует защиты от CSRF на state-changing endpoints.
   - MVP: `SameSite=Lax` + обязательный `Origin` allowlist для cookie-auth запросов.
   - Next: double-submit CSRF (cookie `geo_sim_csrf` + header `X-CSRF-Token`).
- SSE: cookie auth предпочтительнее токена в query-string.
- Admin override header `X-Simulator-Owner` принимается **только при валидном `X-Admin-Token`**.

## 12) Ограничения деплоя (важно заранее)

Runtime сейчас in-process.
Это означает:
- если backend запущен в нескольких worker/process → разные посетители могут “попасть” в разные процессы, и их run’ы/active mapping будет несогласован.

Для прод-демо с анонимными посетителями нужно одно из:
1) запускать backend **single-worker**
2) добавить sticky-sessions на уровне балансировщика
3) вынести runtime в общий storage (Redis/DB) — не входит в задачу

### 12.1 Перезапуск backend (recovery/consistency)

Текущий runtime in-process, поэтому при рестарте backend:
- все “живые” прогоны в памяти прекращаются
- `active_run_id_by_owner` теряется

Если `SIMULATOR_DB_ENABLED=1`, нужно явно определить поведение, чтобы UI/админ не видели «фантомные running»:
- на старте процесса выполнить best-effort reconciliation:
   - найти в `simulator_runs` все записи со `state IN ('running','paused','stopping')`
   - перевести их в `state='error'` (или `stopped`, по договорённости)
   - выставить `stopped_at=now`, `last_error={"reason":"server_restart"}`

Это отдельная небольшая recovery-задача (сейчас общий recovery в проекте симулятор не обслуживает).

### 12.2 Очистка / retention (минимум)

- in-memory структуры (events buffer, active mapping) очищаются автоматически вместе с процессом.
- DB retention задаётся отдельной периодической job (см. общий подход в [docs/ru/simulator/backend/run-storage.md](run-storage.md)).

## 13) План внедрения (итеративно)

1) Backend: `SimulatorActor` + cookie `session/ensure` + `SIMULATOR_SESSION_SECRET` guardrail
2) Backend: `owner_id` на `RunRecord` + per-owner active mapping + enforcement + 409 subtypes
3) Backend: owner-based authZ на run endpoints (403 на чужие run_id)
4) Backend: admin list endpoint + stop-all + admin-only `X-Simulator-Owner` override
5) DB: миграция `simulator_runs.owner_id/owner_kind` + backfill `legacy:unknown`
6) UI: cookie bootstrap + `credentials: 'include'` для HTTP + SSE
7) Tests: добавить кейсы cookie-owner isolation + admin override для параллельных прогонов

## 14) Acceptance criteria

1) Два разных браузера без токена (cookie) могут одновременно:
   - открыть simulator UI в real mode
   - нажать Start
   - получить **разные run_id** и видеть только свой

2) Один посетитель в двух вкладках:
   - Start во второй вкладке не создаёт новый run
   - UI корректно attach’ится к активному run (через `/runs/active`)

3) Анонимный посетитель не может:
   - получить статус чужого run_id
   - подключиться к SSE чужого run_id
   (403)

4) Админ:
   - может получить список всех run’ов через `/admin/runs`
   - может подключиться к SSE любого run и остановить его

5) CLI/тесты:
   - могут создавать runs как раньше с `X-Admin-Token`
   - при заданном `X-Simulator-Owner` могут создавать runs параллельно без 409 per-owner

6) Global limit:
   - если `SIMULATOR_MAX_ACTIVE_RUNS` исчерпан, новый anon Start получает 409 с `conflict_kind=global_active_limit`
   - UI показывает явное сообщение (без attach к чужому run)

## §15. Статус реализации

> Последнее обновление: 2026-02-18

### Полностью реализовано ✅

| Секция | Описание | Файлы | Тесты |
|--------|----------|-------|-------|
| §2 Config | 5 настроек: `SIMULATOR_SESSION_SECRET`, `SIMULATOR_SESSION_TTL_HOURS`, `SIMULATOR_COOKIE_DOMAIN`, `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER`, `SIMULATOR_ANON_VISITORS_ENABLED` | `app/config.py` | `test_simulator_cookie_session.py` |
| §3 SimulatorActor | Dataclass `{kind, owner_id, is_admin, participant_pid}`, priority chain Admin→JWT→Cookie→401 | `app/api/deps.py` | `test_simulator_actor_and_csrf.py` |
| §4 Cookie Session | HMAC-SHA256, формат `v1.<sid>.<iat>.<sig>`, `geo_sim_sid`, Path=/, Secure via X-Forwarded-Proto | `app/core/simulator/session.py`, `app/api/v1/simulator.py` | `test_simulator_cookie_session.py` |
| §5 DB миграция | `owner_id`, `owner_kind` nullable columns, backfill NULL для legacy | `migrations/versions/017_add_owner_to_simulator_runs.py` | — |
| §6 Per-owner isolation | `_active_run_id_by_owner`, per-owner лимит (409 `owner_active_exists`), глобальный лимит (409 `global_active_limit`) | `app/core/simulator/runtime_impl.py`, `app/core/simulator/run_lifecycle.py` | `test_simulator_owner_isolation.py` |
| §7 AuthZ | `_check_run_access()` deny-by-default, пустой owner → только admin | `app/api/v1/simulator.py` | `test_simulator_owner_isolation.py` |
| §8 Admin control plane | `GET /admin/runs`, `POST /admin/runs/stop-all`, ForbiddenException | `app/api/v1/simulator.py` | `test_simulator_owner_isolation.py` |
| §9 X-Simulator-Owner | `.strip()` + regex validation, E009 для невалидных | `app/api/deps.py` | `test_simulator_actor_and_csrf.py` |
| §10 UI | `credentials: 'include'`, session bootstrap, admin controls TopBar | `simulator-ui/v2/src/api/`, `simulator-ui/v2/src/composables/`, `simulator-ui/v2/src/components/` | 217 frontend tests |
| §11 CSRF | Origin check, ForbiddenException с E006, `details.reason=csrf_origin` | `app/api/deps.py` | `test_simulator_actor_and_csrf.py` |
| §12 Recovery | `reconcile_stale_runs()` на startup, rate-limit exemption `/session/ensure` | `app/core/simulator/storage.py`, `app/main.py`, `app/api/deps.py` | — |
| §4 Guardrail | Fail-fast RuntimeError в non-dev при дефолтном секрете | `app/config.py` | `test_simulator_cookie_session.py` |

### Покрытие тестами

- **Backend unit-тесты:** 64 теста (3 файла)
  - `test_simulator_cookie_session.py` — 19 тестов (session, TTL boundary, guardrail)
  - `test_simulator_owner_isolation.py` — 25 тестов (per-owner, conflict_kind, authZ deny-by-default)
  - `test_simulator_actor_and_csrf.py` — 22 теста (actor, CSRF E006, trim, E009)
- **Frontend тесты:** 217 passed

### Найденные и исправленные проблемы (Фаза 10)

1. Per-owner лимит не применялся при `POST /runs` → добавлена проверка с `conflict_kind`
2. Cookie `Path=/api/v1/simulator` → исправлен на `Path=/`
3. Cookie `Secure` не учитывал reverse proxy → добавлена проверка `X-Forwarded-Proto`
4. Session TTL допускал просрочку до `clock_skew_sec` → clock_skew только для будущего iat
5. Guardrail только warning → fail-fast `RuntimeError` вне dev/test
6. CSRF/auth ошибки через `HTTPException` → `ForbiddenException`/`UnauthorizedException` с кодами E006
7. `X-Simulator-Owner` без `.strip()` → добавлен trim + E009 для невалидных
8. `_check_run_access()` разрешал доступ при пустом `owner_id` → deny-by-default для non-admin
9. Миграция ставила `owner_kind='admin'` → исправлено на NULL (по спеке)
10. Admin endpoints через `HTTPException(403)` → `ForbiddenException`
