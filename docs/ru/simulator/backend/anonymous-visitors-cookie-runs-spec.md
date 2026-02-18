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

Каждый запрос к `/api/v1/simulator/*` должен вычислять `actor` и `owner_id` по правилам:

1) Если присутствует валидный `X-Admin-Token` → `actor.kind = admin`, `actor.is_admin = true`.
2) Иначе если есть валидный `Bearer JWT` → `actor.kind = participant`, `owner_id = "pid:<sub>"`.
3) Иначе если есть валидная анонимная cookie сессия → `actor.kind = anon`, `owner_id = "anon:<sid>"`.
4) Иначе → для simulator endpoints:
   - либо возвращаем 401
   - либо (предпочтительнее для UX) выдаём cookie через отдельный endpoint `session/ensure` и дальше клиент повторяет запрос.

**Важно:** для SSE (EventSource) кастомные заголовки в браузере неудобны/ограничены. Cookie-actor даёт самый надёжный способ анонимной авторизации для SSE.

## 4) Cookie сессия (anon)

### 4.1 Cookie формат

Cookie должна быть:
- `HttpOnly` (чтобы JS не мог прочитать)
- `SameSite=Lax` (или `Strict`, если UI и API всегда same-site)
- `Secure=true` в prod
- `Path=/`.

**Имя:** `geo_sim_sid` (v1)

**Значение (stateless, подписанное):**
- `sid` — random 128-bit (hex)
- `iat` — issued-at epoch seconds
- `sig` — HMAC-SHA256 от `sid|iat` с секретом `SIMULATOR_SESSION_SECRET`

Пример (логический, не буквальный):
`base64url({sid,iat,sig})`.

### 4.2 Retention

- Cookie TTL: `SIMULATOR_SESSION_TTL_DAYS` (default 7)
- Сервер принимает cookie, если `now - iat <= TTL`.

### 4.3 Endpoint bootstrap

Добавить endpoint:

`POST /api/v1/simulator/session/ensure`

Ответ:
```json
{ "actor_kind": "anon", "owner_id": "anon:<sid>" }
```

Поведение:
- Если cookie отсутствует/просрочена/невалидна → установить новую и вернуть owner_id
- Если валидна → вернуть текущий owner_id

**Примечание по dev cross-origin:** backend уже включает `allow_credentials=True` и allow-origin regex для localhost. UI запросы должны посылать cookie с `credentials: 'include'`.

## 5) Модель данных: owner поля в run

### 5.1 In-memory

Расширить `RunRecord`:
- `owner_id: str` (обязательное)
- `owner_kind: str` (`anon|participant|admin|cli`), опционально
- `created_by: dict` (best-effort диагностика):
  - `created_by_kind`, `created_by_pid`, `created_by_owner_id`, `created_by_admin: bool`

### 5.2 DB (опционально, если `SIMULATOR_DB_ENABLED=1`)

Расширить таблицу `simulator_runs` (см. `app/db/models/simulator_storage.py`):
- `owner_id TEXT NULL/NOT NULL` (рекомендуется NOT NULL при включенной DB)
- `owner_kind TEXT NULL`

Индексы:
- `INDEX (owner_id, state, created_at)`

Миграция должна быть совместимой с SQLite dev.

## 6) Runtime: active run per owner

### 6.1 Новые структуры

В runtime добавить:
- `active_run_id_by_owner: dict[str, str]` (in-memory)

### 6.2 Правило “active run”

“Активный” — state ∈ {`running`, `paused`, `stopping`}.

Для owner:
- `GET active_run(owner_id)` возвращает run_id только если run ещё не terminal.

### 6.3 Лимиты

Нужно два уровня лимитов:

1) **Per-owner**: `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER` (default 1)
   - предотвращает конфликт “две вкладки одного посетителя”
2) **Global**: `SIMULATOR_MAX_ACTIVE_RUNS` (как сейчас, default 1)
   - защита ресурсов процесса

При `create_run` должны быть enforced оба:
- если owner уже имеет активный → 409 (детали включают owner_id и active_run_id)
- если глобальный лимит достигнут → 409 (детали включают max_active_runs, active_runs)

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

### 8.2 Runs

Существующие эндпоинты сохраняются, но семантика меняется на per-owner:

1) `POST /api/v1/simulator/runs`
   - создаёт run для `actor.owner_id`

2) `GET /api/v1/simulator/runs/active`
   - возвращает active run **только для actor.owner_id**
   - (admin override см. ниже)

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

2) `GET /api/v1/simulator/admin/runs/active`
   - query: `owner_id` (required)
   - ответ: `{ run_id: string|null }`

3) `POST /api/v1/simulator/admin/runs/stop-all` (dev/ops helper)
   - query: `state=running|paused|stopping|*` (default `*`)
   - body: `{ reason?: string }`
   - ответ: `{ stopped: number }`

Примечание: админ и так может останавливать run через существующий `POST /runs/{run_id}/stop`, но stop-all нужен как быстрый “уборщик” после тестов/демо.

## 9) CLI / автотесты (HTTP)

### 9.1 Требование

Автотесты и CLI запускают симулятор через HTTP с `X-Admin-Token`.
После перехода на per-owner модель, если все тесты будут использовать одного owner (`admin`), то параллельные запуски будут конфликтовать по `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER`.

### 9.2 Решение: owner override (только для admin)

Добавить поддержку заголовка:
- `X-Simulator-Owner: <string>`

Правило:
- если `X-Admin-Token` валиден и заголовок присутствует → использовать `owner_id = "cli:<value>"` (или напрямую `<value>` по договорённости)
- если admin token нет → игнорировать заголовок

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
  - Минимум: проверка `Origin`/`Referer` на same-site для `POST` в `/simulator/*`.
  - Рекомендуемо: double-submit CSRF token (отдельная cookie + header).
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

## 13) План внедрения (итеративно)

1) Backend: actor model + cookie session endpoint + owner_id на RunRecord
2) Backend: per-owner active mapping + per-owner лимит + авторизация run endpoints
3) Backend: admin list endpoints + stop-all
4) Backend: admin-only `X-Simulator-Owner` override для CLI/tests
5) Tests: обновить `auth_headers` fixture (или отдельные тесты) чтобы задавать owner override при параллельных/нестабильных прогонах
6) UI: cookie bootstrap + `credentials: 'include'` в fetch + SSE

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
